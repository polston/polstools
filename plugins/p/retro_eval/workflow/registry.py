"""Local transactional record revisions with immutable external artifacts."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

from .errors import EvidenceError, PolicyDenied, RevisionConflict, StoreBusy, StoreRecoveryRequired, UnsupportedVersion, WorkflowError
from .records import CONTENT_CLASSES, Record, RecordRef, canonical, digest, strict_loads, integer

CLASS_ORDER = {name: i for i, name in enumerate((
    "public_synthetic", "private_metadata", "private_redacted", "private_raw"))}


def external_path(path: Path) -> Path:
    path = Path(path).resolve()
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise PolicyDenied("workflow evidence must be outside repositories")
    return path


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise EvidenceError("invalid artifact digest")
    return value


def _read_database_header(path):
    """Inspect format without closing a raw descriptor in SQLite's process.

    On POSIX, closing even a separate descriptor can release another SQLite
    connection's locks. A fresh child owns only its own bounded header read.
    SQLite is opened later; this observation cannot lock out a mode change.
    """
    program = ('import sys\n'
               'with open(sys.argv[1], "rb") as handle:\n'
               '    sys.stdout.buffer.write(handle.read(100))\n')
    try:
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program, str(path)],
            stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvidenceError('cannot inspect registry header safely; '
                            'check file read access and child-process execution') from exc
    return result.stdout


def record_references(value):
    """References have one exact public envelope, wherever nested in payloads."""
    if isinstance(value, dict):
        if set(value) == {"study_scope", "kind", "key", "revision"}:
            yield RecordRef.from_dict(value)
        else:
            for item in value.values():
                yield from record_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from record_references(item)


def artifact_references(value):
    if isinstance(value, dict):
        if "sha256" in value and "content_class" in value:
            _sha(value["sha256"])
            if value["content_class"] not in CONTENT_CLASSES:
                raise EvidenceError("invalid content class")
            yield value
        else:
            for item in value.values():
                yield from artifact_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from artifact_references(item)


class Registry:
    def directory(self, *parts):
        """Create a private output directory without following child symlinks."""
        self._require_writable()
        path = self.root
        for part in parts:
            if not isinstance(part, str) or not part or part in ('.', '..') or '/' in part or '\\' in part:
                raise PolicyDenied('invalid store directory component')
            path = path / part
            if path.is_symlink():
                raise PolicyDenied('store output directory cannot be a symlink')
            path.mkdir(exist_ok=True, mode=0o700)
        return path

    def __init__(self, root: Path, *, timeout=1.0, readonly=False):
        self.root = external_path(root)
        self.readonly = readonly
        if not readonly:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.objects = self.root / "objects"
        for child in ('objects', 'workflow.sqlite', 'workflow.sqlite-journal',
                      'workflow.sqlite-wal', 'workflow.sqlite-shm', 'reports', 'pilots'):
            if (self.root / child).is_symlink():
                raise PolicyDenied('store component cannot be a symlink')
        if readonly:
            # Do not create, migrate, reserve a writer or initialize identity just
            # to inspect committed state. Keep normal locking/change detection:
            # immutable=1 would be wrong for a store another process can update.
            if not self.objects.is_dir() or not (self.root / 'workflow.sqlite').is_file():
                raise EvidenceError('existing workflow store required for read-only inspection')
            # SQLite may create WAL sidecars even through mode=ro. This store
            # uses rollback journaling; reject an observed WAL header before
            # opening SQLite. This is not protection against external concurrent
            # mode changes; stronger nonmutation requires OS write denial.
            # immutable=1 would ignore legitimate concurrent committed changes.
            header = _read_database_header(self.root / 'workflow.sqlite')
            if len(header) != 100 or header[:16] != b'SQLite format 3\x00':
                raise EvidenceError('invalid registry database header')
            if header[18:20] != b'\x01\x01':
                raise UnsupportedVersion('read-only inspection requires rollback journaling; '
                                         'export a private snapshot from an authorized writable store')
            self.db = sqlite3.connect((self.root / 'workflow.sqlite').as_uri() + '?mode=ro',
                                      uri=True, timeout=timeout)
            try:
                self.db.execute('PRAGMA query_only=ON')
                version = self.db.execute('PRAGMA user_version').fetchone()[0]
                if version not in (1, 2):
                    raise UnsupportedVersion('read-only inspection requires registry schema 1 or 2')
                tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                required = {'artifacts', 'records', 'heads', 'dependencies', 'artifact_links',
                            'metadata', 'runs', 'operations'}
                if not required <= tables:
                    raise EvidenceError('incomplete registry schema; read-only inspection cannot initialize it')
                salt = self.db.execute("SELECT value FROM metadata WHERE key='id_salt'").fetchone()
                if salt is None or not isinstance(salt[0], bytes) or len(salt[0]) != 32:
                    raise EvidenceError('registry identity material is missing or invalid')
            except BaseException as exc:
                self.db.close()
                error_code = getattr(exc, 'sqlite_errorcode', None)
                # Python before 3.11 omits native error codes. Only in this
                # read-only query path, the exact error plus a retained journal
                # supports the fallback; journal existence alone proves nothing.
                legacy_rollback = (error_code is None
                    and str(exc) == 'attempt to write a readonly database'
                    and (self.root / 'workflow.sqlite-journal').is_file())
                if (isinstance(exc, sqlite3.OperationalError)
                        and (error_code == getattr(sqlite3, 'SQLITE_READONLY_ROLLBACK', 776)
                             or legacy_rollback)):
                    raise StoreRecoveryRequired(
                        'store needs SQLite rollback recovery; use authorized writable access '
                        'while retaining its journal, then retry read-only inspection') from exc
                if (isinstance(exc, sqlite3.OperationalError)
                        and ((type(error_code) is int and (error_code & 255) in (5, 6))
                             or (error_code is None and str(exc) in (
                                 'database is locked', 'database table is locked')))):
                    raise StoreBusy('registry inspection unavailable within timeout; '
                                    'retry after the active transaction releases its lock') from exc
                raise
            return
        self.objects.mkdir(exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.root / "workflow.sqlite", timeout=timeout)
        try:
            self._initialize()
        except BaseException:
            self.db.close()
            raise

    def _initialize(self):
        """Initialize a writable connection owned by the constructor."""
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise UnsupportedVersion("unsupported registry schema")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS artifacts(
              digest TEXT PRIMARY KEY, content_class TEXT NOT NULL,
              byte_count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS records(
              study TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL,
              revision INTEGER NOT NULL, body BLOB NOT NULL, digest TEXT NOT NULL,
              PRIMARY KEY(study,kind,key,revision));
            CREATE TABLE IF NOT EXISTS heads(
              study TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL,
              revision INTEGER NOT NULL, PRIMARY KEY(study,kind,key),
              FOREIGN KEY(study,kind,key,revision)
                REFERENCES records(study,kind,key,revision));
            CREATE TABLE IF NOT EXISTS dependencies(
              study TEXT, kind TEXT, key TEXT, revision INTEGER,
              target_study TEXT, target_kind TEXT, target_key TEXT, target_revision INTEGER,
              PRIMARY KEY(study,kind,key,revision,target_study,target_kind,target_key,target_revision),
              FOREIGN KEY(study,kind,key,revision) REFERENCES records(study,kind,key,revision),
              FOREIGN KEY(target_study,target_kind,target_key,target_revision)
                REFERENCES records(study,kind,key,revision));
            CREATE TABLE IF NOT EXISTS artifact_links(
              study TEXT, kind TEXT, key TEXT, revision INTEGER, digest TEXT,
              PRIMARY KEY(study,kind,key,revision,digest),
              FOREIGN KEY(study,kind,key,revision) REFERENCES records(study,kind,key,revision),
              FOREIGN KEY(digest) REFERENCES artifacts(digest));
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(
              run_id TEXT PRIMARY KEY, plan BLOB NOT NULL, plan_digest TEXT NOT NULL,
              state TEXT NOT NULL, started REAL, deadline REAL, receipt BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(
              run_id TEXT NOT NULL, unit_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
              state TEXT NOT NULL, reservation BLOB NOT NULL, result BLOB,
              charge_status TEXT NOT NULL, reason TEXT NOT NULL,
              PRIMARY KEY(run_id,unit_key), FOREIGN KEY(run_id) REFERENCES runs(run_id));
        """)
        if version == 0:
            self.db.execute('PRAGMA user_version=1')
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('id_salt',?)", (os.urandom(32),))

    def close(self):
        self.db.close()

    def _require_writable(self):
        if self.readonly:
            raise PolicyDenied('registry was opened for read-only inspection')

    @contextmanager
    def read_snapshot(self):
        """Share one record view; never finish a caller's transaction.

        Artifact files are checked separately and are not part of SQLite's
        snapshot. This read transaction does not reserve the writer slot.
        """
        if self.db.in_transaction:
            yield
            return
        self.db.execute("BEGIN")
        try:
            yield
        finally:
            self.db.rollback()

    @contextmanager
    def transaction(self):
        self._require_writable()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except sqlite3.OperationalError as exc:
            self.db.rollback()
            if "locked" in str(exc) or "busy" in str(exc):
                raise StoreBusy("registry writer unavailable within timeout") from exc
            raise
        except BaseException:
            self.db.rollback()
            raise

    @property
    def id_salt(self):
        return bytes(self.db.execute("SELECT value FROM metadata WHERE key='id_salt'").fetchone()[0])

    @staticmethod
    def _tuple(ref):
        return (ref.study_scope, ref.kind, ref.key, ref.revision)

    def artifact_path(self, sha256):
        _sha(sha256)
        path = self.objects / sha256
        if path.resolve().parent != self.objects.resolve():
            raise PolicyDenied("artifact locator escapes authorized root")
        # A substituted objects directory must not grant a new authorized root.
        if self.objects.resolve() != self.root / "objects":
            raise PolicyDenied("artifact root was replaced")
        return path

    def stage_artifact(self, data: bytes):
        """Publish immutable bytes without making their metadata visible."""
        self._require_writable()
        if not isinstance(data, bytes):
            raise EvidenceError('artifact data must be bytes')
        sha = digest(data)
        target = self.artifact_path(sha)
        descriptor, temp_name = tempfile.mkstemp(dir=self.objects, prefix='.stage-')
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp_name, target)
            except FileExistsError:
                if target.read_bytes() != data:
                    raise EvidenceError('existing immutable artifact is corrupted')
        finally:
            os.unlink(temp_name)
        return sha

    def _bind_artifact(self, sha, content_class, byte_count):
        if content_class not in CONTENT_CLASSES:
            raise EvidenceError('invalid artifact class')
        prior = self.db.execute('SELECT content_class,byte_count FROM artifacts WHERE digest=?', (sha,)).fetchone()
        effective = content_class
        if prior:
            if prior[1] != byte_count:
                raise EvidenceError('artifact length conflict')
            effective = max((prior[0], content_class), key=CLASS_ORDER.__getitem__)
            self.db.execute('UPDATE artifacts SET content_class=? WHERE digest=?', (effective, sha))
        else:
            self.db.execute('INSERT INTO artifacts VALUES(?,?,?)', (sha, effective, byte_count))
        return {'sha256': sha, 'content_class': effective, 'selector': {'kind': 'whole'}}

    def publish_artifact(self, data: bytes, content_class: str):
        if content_class not in CONTENT_CLASSES:
            raise EvidenceError('invalid artifact class')
        sha = self.stage_artifact(data)
        with self.transaction():
            return self._bind_artifact(sha, content_class, len(data))

    def read_artifact(self, reference, allowed_classes=CONTENT_CLASSES):
        if not isinstance(reference, dict):
            raise EvidenceError('artifact reference must be an object')
        sha = _sha(reference.get("sha256"))
        prior = self.db.execute("SELECT content_class,byte_count FROM artifacts WHERE digest=?", (sha,)).fetchone()
        if prior is None:
            raise EvidenceError("artifact is not published")
        if prior[0] not in allowed_classes:
            raise PolicyDenied("consumer cannot read this artifact class")
        try:
            data = self.artifact_path(sha).read_bytes()
        except OSError as exc:
            raise EvidenceError("artifact unavailable") from exc
        if len(data) != prior[1] or digest(data) != sha:
            raise EvidenceError("artifact digest or length mismatch")
        return data, prior[0]

    def _append(self, record, expected_revision):
        if record.ref.kind in ('investigation', 'investigation_event'):
            if self.db.execute('PRAGMA user_version').fetchone()[0] != 2:
                raise UnsupportedVersion('investigation writes require registry format 2')
        ref = record.ref
        identity = self._tuple(ref)
        existing = self.db.execute("SELECT body FROM records WHERE study=? AND kind=? AND key=? AND revision=?", identity).fetchone()
        if existing:
            if bytes(existing[0]) != record.encoded:
                raise RevisionConflict("same record revision has different canonical bytes")
            return False
        row = self.db.execute("SELECT revision FROM heads WHERE study=? AND kind=? AND key=?", identity[:3]).fetchone()
        current = row[0] if row else None
        if current != expected_revision or ref.revision != (current or 0) + 1:
            raise RevisionConflict("expected revision does not match current head %r" % current)
        self.db.execute("INSERT INTO records VALUES(?,?,?,?,?,?)", (*identity, record.encoded, record.sha256))
        self.db.execute("INSERT INTO heads VALUES(?,?,?,?) ON CONFLICT(study,kind,key) DO UPDATE SET revision=excluded.revision", identity)
        return True

    def _link(self, record):
        identity = self._tuple(record.ref)
        for ref in record_references(record.payload):
            if self.get(ref) is None:
                raise EvidenceError("unresolved record dependency")
            self.db.execute("INSERT OR IGNORE INTO dependencies VALUES(?,?,?,?,?,?,?,?)", (*identity, *self._tuple(ref)))
        for ref in artifact_references(record.payload):
            self.read_artifact(ref)
            self.db.execute("INSERT OR IGNORE INTO artifact_links VALUES(?,?,?,?,?)", (*identity, ref["sha256"]))
        if record.ref.kind == 'attempt':
            seen = set()
            current = record
            while current is not None:
                if current.ref in seen:
                    raise EvidenceError('attempt parentage cycle')
                seen.add(current.ref)
                parent = current.payload.get('parent_attempt_ref')
                if parent is None:
                    break
                target = RecordRef.from_dict(parent)
                if target.kind != 'attempt':
                    raise EvidenceError('attempt parent must refer to an attempt')
                current = self.get(target)
                if current is None:
                    raise EvidenceError('unresolved attempt parent')

    def append(self, record: Record, expected_revision):
        with self.transaction():
            self._prepare_investigations([record])
            if self._append(record, expected_revision):
                self._link(record)
                self._check_investigations([record], current=True)
        return record.ref

    def import_records(self, records):
        records = list(records)
        with self.transaction():
            self._import_records(records)
        return [record.ref for record in records]

    def _import_records(self, records):
        """Join an existing publication transaction; caller owns its boundary."""
        self._prepare_investigations(records)
        changed = []
        for record in sorted(records, key=lambda r: self._tuple(r.ref)):
            if self._append(record, record.ref.revision - 1 or None):
                changed.append(record)
        for record in changed:
            self._link(record)
        self._check_investigations(records)

    def _prepare_investigations(self, records):
        """Back up the committed legacy snapshot before the first format-2 write.

        Caller holds BEGIN IMMEDIATE, excluding other writers. A separate read
        connection copies the pre-transaction snapshot, never uncommitted data.
        The backup and immutable objects together permit explicit restoration.
        """
        if not any(r.ref.kind in ('investigation', 'investigation_event') for r in records):
            return
        if self.db.execute('PRAGMA user_version').fetchone()[0] == 2:
            return
        backup_dir = self.directory('backups')
        descriptor, name = tempfile.mkstemp(prefix='format-1-', suffix='.sqlite', dir=backup_dir)
        os.close(descriptor)
        source = sqlite3.connect((self.root / 'workflow.sqlite').as_uri() + '?mode=ro', uri=True)
        target = sqlite3.connect(name)
        try:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise EvidenceError('migration backup failed integrity check')
            if target.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise EvidenceError('migration backup is not legacy format')
        finally:
            target.close()
            source.close()
        with open(name, 'rb') as handle:
            os.fsync(handle.fileno())
            backup_digest = digest(handle.read())
        receipt = {'from': 1, 'to': 2, 'backup': Path(name).name,
                   'backup_digest': backup_digest,
                   'objects': 'retain immutable object directory when restoring'}
        self.db.execute('PRAGMA user_version=2')
        self.db.execute("INSERT INTO metadata VALUES('investigation:migration',?)", (canonical(receipt),))

    def _check_investigations(self, records, *, current=False):
        roots = set()
        for record in records:
            if record.ref.kind == 'investigation':
                roots.add(record.ref)
            elif record.ref.kind == 'investigation_event':
                roots.add(RecordRef.from_dict(record.payload['root_ref']))
        if roots:
            from .investigation_state import rebuild
            for root in sorted(roots, key=self._tuple):
                rebuild(self, root, persist=True, current_events={r.ref for r in records} if current else ())

    def get(self, ref: RecordRef):
        row = self.db.execute("SELECT body,digest FROM records WHERE study=? AND kind=? AND key=? AND revision=?", self._tuple(ref)).fetchone()
        if row is None:
            return None
        if digest(bytes(row[0])) != row[1]:
            raise EvidenceError("registry record digest mismatch")
        return Record(bytes(row[0]))

    def heads(self, kind=None, study_scope=None):
        return self._records(kind, study_scope, current_only=True)

    def history(self, kind=None, study_scope=None):
        """Read retained revisions, including superseded observations.

        Historical exposure cannot be undone by correcting a current record.
        This view authenticates record bytes but does not assert applicability.
        """
        return self._records(kind, study_scope, current_only=False)

    def _records(self, kind, study_scope, *, current_only):
        sql = "SELECT r.body,r.digest FROM records r"
        if current_only:
            sql += " JOIN heads h USING(study,kind,key,revision)"
        clauses, args = [], []
        for name, value in (("kind", kind), ("study", study_scope)):
            if value is not None:
                clauses.append("r." + name + "=?")
                args.append(value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY r.study,r.kind,r.key,r.revision"
        result = []
        for body, sha in self.db.execute(sql, args):
            if digest(bytes(body)) != sha:
                raise EvidenceError("registry record digest mismatch")
            result.append(Record(bytes(body)))
        return result

    def applicability(self, refs):
        """Inspect only requested dependencies; preserve historical payloads."""
        with self.read_snapshot():
            return self._applicability(refs)

    def _investigation_dependencies(self, record):
        """Resolve local action, question and frame identities to exact events."""
        if record.ref.kind != 'investigation_event':
            return []
        typ = record.payload['event_type']
        if typ not in ('result', 'action'):
            return []
        earlier = [r for r in self.heads('investigation_event', record.ref.study_scope)
                   if r.payload['root_ref'] == record.payload['root_ref'] and
                   r.payload['sequence'] < record.payload['sequence']]
        data = record.payload['data']
        if typ == 'result':
            return [r.ref for r in earlier if r.payload['event_type'] == 'action'
                    and r.payload['data']['id'] == data['action_id']]
        frames = [r for r in earlier if r.payload['event_type'] == 'frame']
        dependencies = [max(frames, key=lambda r: r.payload['sequence']).ref
                        if frames else RecordRef.from_dict(record.payload['root_ref'])]
        dependencies.extend(r.ref for r in earlier if r.payload['event_type'] == 'answer'
                            and r.payload['data']['question_id'] in data['questions'])
        return dependencies

    def _applicability(self, refs, *, corrected=None, historical=False):
        queue = [(ref, True) for ref in refs]
        seen, reasons = set(), []
        # Corrections supersede interpretations, never immutable execution facts.
        if corrected is None:
            corrected = {RecordRef.from_dict(r.payload['data']['target'])
                         for r in self.heads('investigation_event')
                         if r.payload['event_type'] == 'correction'}
        while queue:
            ref, require_current = queue.pop()
            identity = self._tuple(ref)
            visit = (*identity, require_current)
            if visit in seen:
                continue
            seen.add(visit)
            record = self.get(ref)
            if record is None:
                reasons.append({"ref": ref.to_dict(), "reason": "missing_record"})
                continue
            head = self.db.execute("SELECT revision FROM heads WHERE study=? AND kind=? AND key=?", identity[:3]).fetchone()
            if require_current and not historical and head[0] != ref.revision:
                reasons.append({"ref": ref.to_dict(), "reason": "superseded_input", "current_revision": head[0]})
            if require_current and ref in corrected:
                reasons.append({'ref': ref.to_dict(), 'reason': 'corrected_interpretation'})
            for artifact in artifact_references(record.payload):
                try:
                    self.read_artifact(artifact)
                    if require_current:
                        from .evidence import resolve_evidence
                        resolution = resolve_evidence(artifact, self)
                        if resolution.status != 'resolved':
                            reasons.append({'ref': ref.to_dict(), 'reason': 'invalid_evidence_selector',
                                            'detail': resolution.reason})
                except (EvidenceError, PolicyDenied) as exc:
                    reasons.append({"ref": ref.to_dict(), "reason": exc.code})
            if ref.kind == 'investigation_event':
                # Membership preserves the originating frame as history. Its
                # existence/integrity matters, not whether every initial input
                # is still the latest. The event's own inputs retain normal
                # applicability semantics. Existing kinds are unchanged.
                queue.append((RecordRef.from_dict(record.payload['root_ref']), False))
                data = record.payload['data']
                if record.payload['event_type'] == 'correction':
                    queue.append((RecordRef.from_dict(data['target']), False))
                    queue.extend((r, require_current) for r in record_references(data['evidence']))
                else:
                    queue.extend((r, require_current) for r in record_references(data))
                # Local action/question identities are implicit references to
                # earlier immutable events, not independent evidence. Follow
                # them so receipts retain their actual input dependencies.
                queue.extend((r, require_current) for r in self._investigation_dependencies(record))
            else:
                queue.extend((r, require_current) for r in record_references(record.payload))
        return {"current": not reasons, "checked_records": len({v[:-1] for v in seen}), "reasons": reasons}

    def export_bundle(self):
        # One read transaction freezes record heads and artifact membership.
        self.db.execute("BEGIN")
        try:
            records = []
            for body, sha in self.db.execute(
                    "SELECT body,digest FROM records ORDER BY study,kind,key,revision"):
                if digest(bytes(body)) != sha:
                    raise EvidenceError("registry record digest mismatch")
                records.append(Record(bytes(body)).to_dict())
            artifacts = []
            for sha, cls in self.db.execute("SELECT digest,content_class FROM artifacts ORDER BY digest"):
                data, effective = self.read_artifact({"sha256": sha})
                artifacts.append({"sha256": sha, "content_class": effective,
                                  "data_base64": base64.b64encode(data).decode("ascii")})
            runs = [{'run_id': r[0], 'plan': strict_loads(r[1]), 'plan_digest': r[2],
                     'state': r[3], 'started': r[4], 'deadline': r[5], 'receipt': strict_loads(r[6])}
                    for r in self.db.execute('SELECT * FROM runs ORDER BY run_id')]
            operations = [{'run_id': r[0], 'unit_key': r[1], 'fingerprint': r[2],
                'state': r[3], 'reservation': strict_loads(r[4]),
                'result': strict_loads(r[5]) if r[5] else None, 'charge_status': r[6], 'reason': r[7]}
                for r in self.db.execute('SELECT * FROM operations ORDER BY run_id,unit_key')]
            metadata = [{'key': k, 'value_base64': base64.b64encode(bytes(v)).decode('ascii')}
                        for k, v in self.db.execute("SELECT key,value FROM metadata WHERE key != 'id_salt' ORDER BY key")]
            self._validate_run_bundle(runs, operations)
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            return {'bundle_format': version, 'private': True,
                    'id_salt_base64': base64.b64encode(self.id_salt).decode('ascii'),
                    'records': records, 'artifacts': artifacts,
                    'runs': runs, 'operations': operations, 'metadata': metadata}
        finally:
            self.db.rollback()

    @staticmethod
    def _validate_run_bundle(runs, operations):
        from .runner import Limits, _clock
        if not isinstance(runs, list) or not isinstance(operations, list):
            raise EvidenceError('invalid run bundle')
        ids = {}
        for run in runs:
            if set(run) != {'run_id', 'plan', 'plan_digest', 'state', 'started', 'deadline', 'receipt'}:
                raise EvidenceError('invalid run envelope')
            if run['run_id'] in ids or not isinstance(run['run_id'], str) or not run['run_id']:
                raise EvidenceError('duplicate or invalid run identity')
            if digest(canonical(run['plan'])) != run['plan_digest']:
                raise EvidenceError('run plan digest mismatch')
            limits = Limits.from_dict(run['plan'].get('limits', {}))
            if 'invocation_ref' in run['plan']:
                RecordRef.from_dict(run['plan']['invocation_ref'])
            if run['state'] not in ('running', 'partial', 'completed', 'failed', 'canceled'):
                raise EvidenceError('invalid run state')
            start, deadline = _clock(run['started']), _clock(run['deadline'])
            if deadline != start + limits.runtime_seconds:
                raise EvidenceError('run deadline violates its fixed limit')
            if not isinstance(run['receipt'], dict):
                raise EvidenceError('invalid run receipt')
            ids[run['run_id']] = limits
        seen = set()
        usage = {key: {} for key in ids}
        for operation in operations:
            if set(operation) != {'run_id', 'unit_key', 'fingerprint', 'state', 'reservation', 'result', 'charge_status', 'reason'}:
                raise EvidenceError('invalid operation envelope')
            identity = (operation['run_id'], operation['unit_key'])
            if identity in seen or identity[0] not in ids:
                raise EvidenceError('duplicate operation or missing run')
            seen.add(identity)
            if operation['state'] not in ('reserved', 'completed', 'failed', 'uncertain'):
                raise EvidenceError('invalid operation state')
            if operation['charge_status'] not in ('unknown', 'not_applicable', 'billed', 'not_billed'):
                raise EvidenceError('invalid operation charge state')
            if not isinstance(operation['reservation'], dict):
                raise EvidenceError('invalid resource reservation')
            for name, value in operation['reservation'].items():
                integer(value, name)
                if name == 'runtime_seconds' or not hasattr(ids[identity[0]], name):
                    raise EvidenceError('unknown resource reservation')
                usage[identity[0]][name] = usage[identity[0]].get(name, 0) + value
                if usage[identity[0]][name] > getattr(ids[identity[0]], name):
                    raise EvidenceError('run reservation exceeds its budget')
            if operation['state'] == 'completed' and not isinstance(operation['result'], dict):
                raise EvidenceError('completed operation lacks a receipt')
        for run in runs:
            if run['state'] == 'completed' and any(o['state'] != 'completed' for o in operations if o['run_id'] == run['run_id']):
                raise EvidenceError('completed run has unfinished operations')

    def import_bundle(self, bundle):
        if not isinstance(bundle, dict) or type(bundle.get('bundle_format')) is not int or bundle['bundle_format'] not in (1, 2):
            raise UnsupportedVersion('unsupported bundle version')
        if bundle.get('private') is not True:
            raise PolicyDenied('only private workflow bundles are supported')
        try:
            records = [Record.from_dict(r) for r in bundle['records']]
            salt = base64.b64decode(bundle['id_salt_base64'], validate=True)
            artifacts = [(a, base64.b64decode(a['data_base64'], validate=True))
                         for a in bundle['artifacts']]
            metadata = [(item['key'], base64.b64decode(item['value_base64'], validate=True))
                        for item in bundle.get('metadata', [])]
        except WorkflowError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise EvidenceError('invalid bundle encoding or record') from exc
        if len(salt) != 32:
            raise EvidenceError('invalid bundle identity material')
        if bundle['bundle_format'] == 1 and any(r.ref.kind in ('investigation', 'investigation_event') for r in records):
            raise UnsupportedVersion('legacy bundle contains investigation records')
        if any(not isinstance(k, str) or not k or k == 'id_salt' for k, _ in metadata) or len({k for k, _ in metadata}) != len(metadata):
            raise EvidenceError('invalid bundle metadata')
        for artifact, data in artifacts:
            if digest(data) != artifact['sha256'] or artifact['content_class'] not in CONTENT_CLASSES:
                raise EvidenceError('invalid bundle artifact')
        runs, operations = bundle.get('runs', []), bundle.get('operations', [])
        self._validate_run_bundle(runs, operations)
        # Only unbound immutable bytes may remain after a rejected import.
        for artifact, data in artifacts:
            self.stage_artifact(data)
        with self.transaction():
            occupied = self.db.execute('SELECT (SELECT count(*) FROM records)+(SELECT count(*) FROM runs)').fetchone()[0]
            if occupied and self.id_salt != salt:
                raise RevisionConflict('different identity scopes need explicit mapping')
            self._prepare_investigations(records)
            for artifact, data in artifacts:
                # Import is a private collection boundary, not authority to
                # disclose bytes. A self-declared synthetic label cannot grant
                # external-provider access in a previously empty registry.
                content_class = artifact['content_class']
                if content_class == 'public_synthetic':
                    content_class = 'private_metadata'
                self._bind_artifact(artifact['sha256'], content_class, len(data))
            changed = []
            for record in sorted(records, key=lambda r: self._tuple(r.ref)):
                if self._append(record, record.ref.revision - 1 or None):
                    changed.append(record)
            for record in changed:
                self._link(record)
            for run in runs:
                if 'invocation_ref' in run['plan'] and self.get(RecordRef.from_dict(run['plan']['invocation_ref'])) is None:
                    raise EvidenceError('invocation record is missing')
                values = (run['run_id'], canonical(run['plan']), run['plan_digest'], run['state'],
                          run['started'], run['deadline'], canonical(run['receipt']))
                existing = self.db.execute('SELECT * FROM runs WHERE run_id=?', (run['run_id'],)).fetchone()
                if existing and tuple(existing) != values:
                    raise RevisionConflict('existing run differs from imported receipt')
                self.db.execute('INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?,?)', values)
            for operation in operations:
                for ref in artifact_references(operation.get('result')):
                    self.read_artifact(ref)
                values = (operation['run_id'], operation['unit_key'], operation['fingerprint'],
                    operation['state'], canonical(operation['reservation']),
                    canonical(operation['result']) if operation['result'] is not None else None,
                    operation['charge_status'], operation['reason'])
                existing = self.db.execute('SELECT * FROM operations WHERE run_id=? AND unit_key=?', values[:2]).fetchone()
                if existing and tuple(existing) != values:
                    raise RevisionConflict('existing operation differs from imported receipt')
                self.db.execute('INSERT OR IGNORE INTO operations VALUES(?,?,?,?,?,?,?,?)', values)
            for key, value in metadata:
                if key.startswith('investigation:'):
                    # Projections and local backup receipts are not portable
                    # authority. Replay events and keep this store's own backup.
                    continue
                existing = self.db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
                if existing and bytes(existing[0]) != value:
                    raise RevisionConflict('existing planning metadata differs from import')
                self.db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)', (key, value))
            self.db.execute("UPDATE metadata SET value=? WHERE key='id_salt'", (salt,))
            self._check_investigations(records)
        return [record.ref for record in records]
