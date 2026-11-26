"""Loopback-only annotation workspace and stdlib HTTP adapter."""

from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import json
import os
import secrets
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .annotation import (_assessment_values, _external, _load_packet,
                         _packet_fingerprint, _rows_fingerprint,
                         _validate_assessments)
from .annotation_formatting import evidence_blocks
from .annotation_lock import AnnotationBusy, packet_write_lock
from .catalog import (load_annotation_protocol_catalogue,
                      load_rubric_catalogue)


class AnnotationConflict(RuntimeError):
    """The packet changed after a browser read it."""


def _validate_case_evidence(protocol, case):
    if protocol.id == "interpretation-grounding":
        required = ("review_kind", "situation_summary", "interpretation",
                    "rationale", "expected_action")
        if not all(str(case.get(field) or "").strip() for field in required):
            raise ValueError("interpretation review case is incomplete")
        if case["review_kind"] not in {
                "user_understanding", "agent_judgment"}:
            raise ValueError("interpretation review kind is unsupported")
        return
    if protocol.id != "duplicate-work-taxonomy" or protocol.version < 4:
        return
    context = str(case.get("context") or "")
    focal = str(case.get("user_turn") or "")
    evaluator_phrases = ("assess whether", "choose one", "classify why",
                         "proposed diagnosis")
    if any(value in focal.lower() for value in evaluator_phrases):
        raise ValueError("repeat review evidence contains evaluator instructions")
    if ("### Prior call" not in context
            or "### Intervening operations" not in context
            or "### Current repeated call" not in focal):
        raise ValueError("repeat review case has invalid evidence roles")


class AnnotationWorkspace:
    def __init__(self, *, source: Path, manifest_path: Path, rubrics_path: Path,
                 protocols_path: Path, dashboard=None):
        self.source = Path(source).resolve()
        self.manifest_path = Path(manifest_path)
        _external(self.source)
        _external(self.manifest_path)
        rubrics = load_rubric_catalogue(Path(rubrics_path))
        try:
            raw_manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            rubric_id = str(raw_manifest["rubric_id"])
            rubric_version = int(raw_manifest["rubric_version"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("annotation UI requires a protocol-bound manifest") from exc
        rubric = next((item for item in rubrics.rubrics
                       if item.id == rubric_id and item.version == rubric_version), None)
        if rubric is None:
            raise ValueError("annotation manifest rubric is absent from catalogue")
        self.manifest, _ = _load_packet(
            self.source, self.manifest_path, rubric_id=rubric.id,
            rubric_version=rubric.version)
        try:
            protocol_id = str(self.manifest["annotation_protocol_id"])
            protocol_version = int(self.manifest["annotation_protocol_version"])
            protocol_sha256 = str(self.manifest["annotation_protocol_sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("annotation UI requires a protocol-bound manifest") from exc
        protocol = load_annotation_protocol_catalogue(
            Path(protocols_path), rubrics).get(protocol_id, protocol_version)
        if (protocol.sha256 != protocol_sha256
                or protocol.rubric_id != rubric.id
                or protocol.rubric_version != rubric.version):
            raise ValueError("annotation protocol binding does not match catalogue")
        self.rubric = rubric
        self.protocol = protocol
        self.dashboard = dashboard
        self._lock = threading.Lock()

    def _rows_with_revision(self):
        raw = self.source.read_bytes()
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
        rows = list(reader)
        if _rows_fingerprint(tuple(reader.fieldnames or ()), rows) != self.manifest["sample_sha256"]:
            raise AnnotationConflict("annotation packet evidence changed")
        if not rows or any(not str(row.get("case_id") or "") for row in rows):
            raise ValueError("annotation packet has no usable cases")
        if len({row["case_id"] for row in rows}) != len(rows):
            raise ValueError("annotation packet contains duplicate case id")
        _validate_assessments(rows, self.rubric.id)
        return rows, hashlib.sha256(raw).hexdigest()

    def snapshot(self):
        with self._lock:
            rows, revision = self._rows_with_revision()
        assessment_packet = "assessment" in rows[0]
        completed = sum(bool(str(row.get(
            "assessment" if assessment_packet else "human_label") or "").strip())
                        for row in rows)
        cases = []
        for row in rows:
            case = {field: str(value or "") for field, value in row.items()}
            _validate_case_evidence(self.protocol, case)
            case["context_blocks"] = evidence_blocks(case["context"])
            case["user_turn_blocks"] = evidence_blocks(case["user_turn"])
            cases.append(case)
        default_presentation = {
            "context_label": "Preceding assistant context",
            "focal_label": "User reply",
            "review_question": self.protocol.human_instruction,
        }
        presentation = self.protocol.extensions.get(
            "presentation", default_presentation)
        if (not isinstance(presentation, dict)
                or not all(str(presentation.get(key) or "").strip() for key in (
                    "context_label", "focal_label", "review_question"))):
            raise ValueError("annotation protocol presentation is incomplete")
        snapshot = {
            "schema_version": 1,
            "dataset_id": self.manifest["dataset_id"],
            "revision": revision,
            "progress": {"completed": completed, "total": len(rows)},
            "protocol": {
                "id": self.protocol.id,
                "version": self.protocol.version,
                "sha256": self.protocol.sha256,
                "decision_order": list(self.protocol.decision_order),
                "definitions": dict(self.protocol.definitions),
                "human_instruction": self.protocol.human_instruction,
                "label_prompts": dict(self.protocol.label_prompts),
                "tie_breaks": list(self.protocol.tie_breaks),
                "presentation": {
                    key: str(presentation[key]) for key in (
                        "context_label", "focal_label", "review_question")
                },
            },
            "cases": cases,
        }
        if self.dashboard is not None:
            snapshot["dashboard"] = self.dashboard
        return snapshot

    def update(self, *, case_id: str, label: str, notes: str,
               assessment=None,
               expected_revision: str):
        label = str(label).strip()
        if label and label not in self.rubric.labels:
            raise ValueError("unsupported label")
        notes = str(notes)
        if len(notes) > 2000:
            raise ValueError("notes exceed 2000 characters")
        with packet_write_lock(self.source, local_lock=self._lock):
            rows, revision = self._rows_with_revision()
            if expected_revision != revision:
                raise AnnotationConflict("annotation packet changed; reload before saving")
            matches = [row for row in rows if row["case_id"] == case_id]
            if len(matches) != 1:
                raise ValueError("unknown case id")
            target = matches[0]
            if assessment is None and "assessment" in target:
                assessment = target["assessment"] or ""
            if assessment is not None:
                assessment = str(assessment).strip()
                if "assessment" not in target:
                    raise ValueError("assessment is unsupported for this packet")
                grounding = self.protocol.id == "interpretation-grounding"
                allowed = _assessment_values(self.rubric.id)
                if assessment not in allowed:
                    raise ValueError("unsupported assessment")
                proposed = str(target.get("proposed_label") or "")
                if grounding and label != assessment:
                    raise ValueError("interpretation assessment must match its label")
                if not grounding and assessment == "correct" and label != proposed:
                    raise ValueError("correct assessment must accept the proposal")
                if not grounding and assessment == "incorrect" and (
                        not label or label == proposed):
                    raise ValueError("incorrect assessment requires a correction label")
                if not grounding and assessment == "unsure" and label:
                    raise ValueError("unsure assessment cannot assert a label")
                target["assessment"] = assessment
            target["human_label"] = label
            target["notes"] = notes
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".annotation-", suffix=".csv", dir=self.source.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary = Path(temporary_name)
                if _packet_fingerprint(temporary) != self.manifest["sample_sha256"]:
                    raise AnnotationConflict("save would alter immutable evidence")
                os.replace(temporary, self.source)
            finally:
                try:
                    Path(temporary_name).unlink()
                except FileNotFoundError:
                    pass
        return self.snapshot()


class _AnnotationServer(ThreadingHTTPServer):
    daemon_threads = True


def _handler_type(workspace, assets_dir, csrf_token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RetroAnnotation/1"

        def log_message(self, _format, *_args):
            return

        def _headers(self, status, content_type, length):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()

        def _send_json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _own_hosts(self):
            port = self.server.server_address[1]
            return {"127.0.0.1:%d" % port, "localhost:%d" % port,
                    "[::1]:%d" % port}

        def _host_refused(self):
            # A rebound DNS name reaches this socket with its own name in Host,
            # and a page it serves sends a matching Origin, so both are checked
            # against the server's fixed loopback forms, never against each other.
            if self.headers.get("Host") in self._own_hosts():
                return False
            self._send_json(403, {"error": "invalid_host"})
            return True

        def do_GET(self):
            if self._host_refused():
                return
            path = urlsplit(self.path).path
            if path == "/api/state":
                try:
                    payload = workspace.snapshot()
                except AnnotationConflict as exc:
                    self._send_json(409, {"error": "conflict", "message": str(exc)})
                    return
                payload["csrf_token"] = csrf_token
                self._send_json(200, payload)
                return
            assets = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            }
            asset = assets.get(path)
            if asset is None:
                self._send_json(404, {"error": "not_found"})
                return
            try:
                body = (assets_dir / asset[0]).read_bytes()
            except OSError:
                self._send_json(500, {"error": "asset_unavailable"})
                return
            self._headers(200, asset[1], len(body))
            self.wfile.write(body)

        def do_POST(self):
            if self._host_refused():
                return
            if urlsplit(self.path).path != "/api/labels":
                self._send_json(404, {"error": "not_found"})
                return
            if self.headers.get("X-Retro-CSRF") != csrf_token:
                self._send_json(403, {"error": "invalid_csrf"})
                return
            origin = self.headers.get("Origin")
            if origin and origin not in {
                    "http://%s" % host for host in self._own_hosts()}:
                self._send_json(403, {"error": "invalid_origin"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length < 1 or length > 8192:
                self._send_json(413, {"error": "invalid_payload_size"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                state = workspace.update(
                    case_id=payload["case_id"], label=payload["label"],
                    notes=payload.get("notes", ""),
                    assessment=payload.get("assessment"),
                    expected_revision=payload["expected_revision"])
            except AnnotationConflict as exc:
                self._send_json(409, {"error": "conflict", "message": str(exc)})
                return
            except AnnotationBusy as exc:
                self._send_json(503, {"error": "writer_busy", "message": str(exc)})
                return
            except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
                self._send_json(400, {"error": "invalid_request", "message": str(exc)})
                return
            state["csrf_token"] = csrf_token
            self._send_json(200, state)

    return Handler


def create_server(workspace, *, host="127.0.0.1", port=0, assets_dir=None):
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("annotation server must bind to a loopback address")
    except ValueError as exc:
        if "loopback" in str(exc):
            raise
        raise ValueError("annotation server host must be a loopback IP") from exc
    assets_dir = Path(assets_dir or Path(__file__).resolve().parents[1]
                      / "ui" / "annotation")
    token = secrets.token_urlsafe(32)
    return _AnnotationServer((host, int(port)),
                             _handler_type(workspace, assets_dir, token))


def serve_annotation_ui(workspace, *, host="127.0.0.1", port=0,
                        open_browser=True):
    server = create_server(workspace, host=host, port=port)
    url = "http://%s:%d/" % server.server_address
    print(json.dumps({"url": url, "privacy": "loopback-only"}), flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
