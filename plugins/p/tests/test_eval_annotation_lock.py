"""Cooperating annotation writers preserve acknowledged edits across processes."""

import json
import multiprocessing
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.annotation_lock import AnnotationBusy, packet_lock_path, packet_write_lock
from retro_eval.annotation_ui import AnnotationWorkspace, create_server


def _workspace(source, manifest):
    return AnnotationWorkspace(
        source=Path(source), manifest_path=Path(manifest),
        rubrics_path=PLUGIN_ROOT / "rubrics" / "rubrics.json",
        protocols_path=PLUGIN_ROOT / "rubrics" / "annotation-protocols.json")


def _writer(source, manifest, case_id, label, revision, started, captured,
            release, results):
    workspace = _workspace(source, manifest)
    original = workspace._rows_with_revision
    first = True

    def read():
        nonlocal first
        value = original()
        if first:
            first = False
            captured.set()
            if release is not None and not release.wait(10):
                raise RuntimeError("test writer was not released")
        return value

    workspace._rows_with_revision = read
    started.set()
    try:
        workspace.update(case_id=case_id, label=label, notes="",
                         expected_revision=revision)
        results.put("saved")
    except Exception as exc:
        results.put(type(exc).__name__)


def _hold_lock(source, ready):
    with packet_write_lock(source):
        ready.set()
        time.sleep(10)


class AnnotationWriterLockTests(unittest.TestCase):
    def setUp(self):
        from test_eval_annotation_ui import AnnotationWorkspaceTests
        self.fixture = AnnotationWorkspaceTests()
        self.fixture.setUp()
        self.source = self.fixture.packet
        self.manifest = self.fixture.manifest
        self.context = multiprocessing.get_context("spawn")

    def tearDown(self):
        self.fixture.tearDown()

    def test_independent_process_writers_reject_stale_update_then_retry(self):
        workspace = _workspace(self.source, self.manifest)
        state = workspace.snapshot()
        started = [self.context.Event(), self.context.Event()]
        captured = [self.context.Event(), self.context.Event()]
        release = self.context.Event()
        results = self.context.Queue()
        processes = [self.context.Process(target=_writer, args=(
            self.source, self.manifest, state["cases"][i]["case_id"],
            ("approval", "correction")[i], state["revision"],
            started[i], captured[i], release if i == 0 else None, results))
            for i in range(2)]
        try:
            processes[0].start()
            self.assertTrue(captured[0].wait(5))
            processes[1].start()
            self.assertTrue(started[1].wait(5))
            self.assertFalse(captured[1].wait(0.3))
            release.set()
            outcomes = [results.get(timeout=5), results.get(timeout=5)]
            self.assertCountEqual(["saved", "AnnotationConflict"], outcomes)
            for process in processes:
                process.join(5)
                self.assertEqual(0, process.exitcode)
        finally:
            release.set()
            for process in processes:
                if process.pid is not None and process.is_alive():
                    process.terminate()
                    process.join(5)
            results.close()
            results.join_thread()
        current = workspace.snapshot()
        updated = workspace.update(
            case_id=current["cases"][1]["case_id"], label="correction", notes="",
            expected_revision=current["revision"])
        self.assertEqual(["approval", "correction"],
                         [case["human_label"] for case in updated["cases"]])

    def test_contention_is_bounded_and_process_exit_releases_lock(self):
        ready = self.context.Event()
        process = self.context.Process(target=_hold_lock,
                                       args=(self.source, ready))
        before = self.source.read_bytes()
        process.start()
        try:
            self.assertTrue(ready.wait(5))
            with self.assertRaises(AnnotationBusy):
                with packet_write_lock(self.source, timeout=0.05):
                    self.fail("contending writer acquired the lock")
            process.terminate()
            process.join(5)
            self.assertFalse(process.is_alive())
            with packet_write_lock(self.source, timeout=0.2):
                pass
            self.assertEqual(before, self.source.read_bytes())
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)

    def test_exception_releases_lock_and_invalid_bounds_do_not_open_it(self):
        lock = packet_lock_path(self.source)
        for timeout in (True, -1, float("nan"), float("inf"), 10 ** 500, "1"):
            with self.subTest(timeout_type=type(timeout).__name__):
                with self.assertRaises(ValueError):
                    with packet_write_lock(self.source, timeout=timeout):
                        self.fail("invalid timeout accepted")
                self.assertFalse(lock.exists())
        with self.assertRaisesRegex(RuntimeError, "authored failure"):
            with packet_write_lock(self.source):
                raise RuntimeError("authored failure")
        with packet_write_lock(self.source, timeout=0):
            pass
        self.assertEqual(b"", lock.read_bytes())

    def test_busy_http_save_preserves_packet_and_can_be_retried(self):
        workspace = _workspace(self.source, self.manifest)
        server = create_server(workspace)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://%s:%d" % server.server_address
        try:
            with urlopen(base + "/api/state", timeout=2) as response:
                state = json.load(response)
            request = Request(base + "/api/labels", method="POST",
                              data=json.dumps({
                                  "case_id": state["cases"][0]["case_id"],
                                  "label": "correction", "notes": "Retained note",
                                  "expected_revision": state["revision"],
                              }).encode("utf-8"),
                              headers={"Content-Type": "application/json",
                                       "X-Retro-CSRF": state["csrf_token"]})
            before = self.source.read_bytes()
            with packet_write_lock(self.source), patch(
                    "retro_eval.annotation_ui.packet_write_lock",
                    lambda source, **kwargs: packet_write_lock(source, timeout=0.05, **kwargs)):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=2)
                self.assertEqual(503, caught.exception.code)
                self.assertEqual("writer_busy", json.load(caught.exception)["error"])
                caught.exception.close()
                self.assertEqual(before, self.source.read_bytes())
            with urlopen(request, timeout=2) as response:
                saved = json.load(response)
            self.assertEqual("correction", saved["cases"][0]["human_label"])
            self.assertEqual("Retained note", saved["cases"][0]["notes"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_symlink_source_keeps_alias_and_updates_its_target(self):
        alias = self.source.with_name("alias.csv")
        try:
            alias.symlink_to(self.source.name)
        except OSError as exc:
            self.skipTest("symlink creation unavailable: %s" % type(exc).__name__)
        workspace = _workspace(alias, self.manifest)
        state = workspace.snapshot()
        workspace.update(case_id=state["cases"][0]["case_id"], label="correction",
                         notes="", expected_revision=state["revision"])
        self.assertTrue(alias.is_symlink())
        self.assertEqual(alias.read_bytes(), self.source.read_bytes())
        self.assertEqual("correction",
                         _workspace(self.source, self.manifest).snapshot()["cases"][0]["human_label"])

    def test_symlink_lock_is_rejected_without_changing_its_target(self):
        victim = self.source.with_name("retained.txt")
        victim.write_bytes(b"Retained")
        lock = packet_lock_path(self.source)
        try:
            lock.symlink_to(victim.name)
        except OSError as exc:
            self.skipTest("symlink creation unavailable: %s" % type(exc).__name__)
        with self.assertRaises(ValueError):
            with packet_write_lock(self.source):
                self.fail("symlink lock accepted")
        self.assertEqual(b"Retained", victim.read_bytes())

    def test_waiting_readers_and_writers_share_the_contention_budget(self):
        local = threading.Lock()
        local.acquire()
        start = time.monotonic()
        try:
            with self.assertRaises(AnnotationBusy):
                with packet_write_lock(self.source, timeout=0.05, local_lock=local):
                    self.fail("busy reader was bypassed")
            self.assertLess(time.monotonic() - start, 1)
            # Failure to acquire the reader mutex must release the file lock.
            with packet_write_lock(self.source, timeout=0):
                pass
        finally:
            local.release()

    def test_queued_saves_do_not_multiply_the_contention_timeout(self):
        workspace = _workspace(self.source, self.manifest)
        state = workspace.snapshot()
        barrier = threading.Barrier(4)
        outcomes = []
        def save():
            barrier.wait()
            started = time.monotonic()
            try:
                workspace.update(case_id=state["cases"][0]["case_id"],
                                 label="correction", notes="",
                                 expected_revision=state["revision"])
                result = "saved"
            except AnnotationBusy:
                result = "busy"
            outcomes.append((result, time.monotonic() - started))
        before = self.source.read_bytes()
        with packet_write_lock(self.source), patch(
                "retro_eval.annotation_ui.packet_write_lock",
                lambda source, **kwargs: packet_write_lock(
                    source, timeout=0.3, **kwargs)):
            threads = [threading.Thread(target=save) for _ in range(3)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(3)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(3, len(outcomes))
        self.assertTrue(all(result == "busy" for result, _ in outcomes))
        self.assertLess(max(elapsed for _, elapsed in outcomes), 0.75)
        self.assertEqual(before, self.source.read_bytes())

    def test_long_packet_names_and_case_unicode_aliases(self):
        for name in ("a" * 255, "é" * 120 + ".csv"):
            source = self.source.with_name(name)
            try:
                source.write_bytes(self.source.read_bytes())
            except OSError as exc:
                self.skipTest("filesystem filename limit: %s" % type(exc).__name__)
            workspace = _workspace(source, self.manifest)
            state = workspace.snapshot()
            updated = workspace.update(
                case_id=state["cases"][0]["case_id"], label="correction",
                notes="", expected_revision=state["revision"])
            self.assertEqual("correction", updated["cases"][0]["human_label"])
        # Conservative aliasing may serialize distinct names on a case-sensitive
        # filesystem, but must never give these aliases independent lock names.
        self.assertEqual(packet_lock_path(self.source.with_name("É.csv")),
                         packet_lock_path(self.source.with_name("e\u0301.CSV")))
