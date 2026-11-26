"""Bounded local runs with durable operation reservations and partial receipts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import time

from .errors import BudgetExhausted, PolicyDenied, RevisionConflict, WorkflowError
from .records import RecordRef, canonical, digest, integer, strict_loads
from .registry import artifact_references, record_references


@dataclass(frozen=True)
class Limits:
    runtime_seconds: int = 600
    cases: int = 100
    source_files: int = 1000
    captured_bytes: int = 100 * 1024 * 1024
    derived_bytes: int = 25 * 1024 * 1024
    cards: int = 3
    provider_calls: int = 0
    human_annotations: int = 0

    def __post_init__(self):
        for name, value in asdict(self).items():
            integer(value, name, 0 if name in ('provider_calls', 'human_annotations') else 1)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise WorkflowError('run limits must be an object')
        try:
            return cls(**value)
        except TypeError as exc:
            raise WorkflowError('unknown run limit') from exc


def _clock(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise WorkflowError('clock must be finite nonnegative epoch seconds')
    return value


def _provider_accounting(operations):
    unverified = []
    for operation in operations:
        if operation['charge_status'] == 'not_applicable':
            continue
        calls = operation['reservation'].get('provider_calls')
        if type(calls) is not int or calls < 1:
            unverified.append(operation['unit_key'])
            continue
        if operation['state'] != 'completed':
            continue
        marker = (operation['result'] or {}).get('provider_admission')
        if (not isinstance(marker, dict) or set(marker) != {'version', 'reserved_calls'}
                or type(marker.get('version')) is not int or marker['version'] != 1
                or type(marker.get('reserved_calls')) is not int
                or marker['reserved_calls'] != calls):
            unverified.append(operation['unit_key'])
    return {'unverified_operations': unverified,
            'scope': 'recorded helper admission, not external billing'}


class Runner:
    """Callbacks are installed trusted code, not transcript-supplied commands.

    Reservation-before-call prevents free retries of potentially billed work.
    It cannot promise exactly-once external effects across a process crash.
    """
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
    def begin(self, plan, *, run_id=None, started_at=None):
        limits = Limits.from_dict(plan.get('limits', {}))
        if 'invocation_ref' in plan and self.store.get(RecordRef.from_dict(plan['invocation_ref'])) is None:
            raise WorkflowError('invocation record is missing')
        encoded = canonical(plan)
        sha = digest(encoded)
        run_id = run_id or sha[:24]
        now = _clock(self.clock())
        started_at = now if started_at is None else _clock(started_at)
        if started_at > now:
            raise WorkflowError('run cannot start in the future')
        with self.store.transaction():
            row = self.store.db.execute('SELECT plan_digest FROM runs WHERE run_id=?', (run_id,)).fetchone()
            if row:
                if row[0] != sha:
                    raise RevisionConflict('run identity has different planning inputs')
            else:
                receipt = canonical({'reason': '', 'limits': asdict(limits)})
                self.store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?)',
                    (run_id, encoded, sha, 'running', started_at, started_at + limits.runtime_seconds, receipt))
        return self.status(run_id)

    def status(self, run_id):
        row = self.store.db.execute('SELECT plan,plan_digest,state,started,deadline,receipt FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise WorkflowError('unknown run')
        if digest(bytes(row[0])) != row[1]:
            raise WorkflowError('run plan digest mismatch')
        operations = []
        used = {name: 0 for name in asdict(Limits()) if name != 'runtime_seconds'}
        for key, fp, state, reservation, result, charge, reason in self.store.db.execute(
                'SELECT unit_key,fingerprint,state,reservation,result,charge_status,reason FROM operations WHERE run_id=? ORDER BY unit_key', (run_id,)):
            reservation = strict_loads(reservation)
            for name, amount in reservation.items():
                used[name] += amount
            operations.append({'unit_key': key, 'fingerprint': fp, 'state': state,
                'reservation': reservation, 'result': strict_loads(result) if result else None,
                'charge_status': charge, 'reason': reason})
        return {'run_id': run_id, 'plan': strict_loads(row[0]), 'state': row[2],
                'started': row[3], 'deadline': row[4], 'receipt': strict_loads(row[5]),
                'used': used, 'operations': operations,
                'usage_scope': 'all_run_operation_reservations',
                'provider_accounting': _provider_accounting(operations)}

    def list_runs(self):
        return [self.status(row[0]) for row in self.store.db.execute('SELECT run_id FROM runs ORDER BY started,run_id')]

    def result_applicability(self, run_id):
        """Check exact committed result dependencies, never substitute new heads."""
        status = self.status(run_id)
        refs, reasons = list(record_references(status['plan'])), []
        artifacts = []
        for operation in status['operations']:
            if operation['state'] != 'completed':
                continue
            for ref in artifact_references(operation['result']):
                try:
                    data, _ = self.store.read_artifact(ref)
                    if operation['unit_key'].startswith('render:'):
                        continue
                    payload = strict_loads(data)
                    refs.extend(record_references(payload))
                    artifacts.extend(artifact_references(payload))
                except WorkflowError as exc:
                    reasons.append({'unit_key': operation['unit_key'], 'reason': exc.code})
        for ref in artifacts:
            try:
                self.store.read_artifact(ref)
            except WorkflowError as exc:
                reasons.append({'artifact_digest': ref['sha256'], 'reason': exc.code})
        applicability = self.store.applicability(refs)
        return {'current': applicability['current'] and not reasons,
                'checked_records': applicability['checked_records'],
                'reasons': applicability['reasons'] + reasons}

    def finish(self, run_id, state='completed', reason=''):
        if state not in ('completed', 'partial', 'failed', 'canceled'):
            raise WorkflowError('invalid terminal run state')
        with self.store.transaction():
            status = self.status(run_id)
            if status['state'] in ('completed', 'failed', 'canceled'):
                return status
            operations = status['operations']
            if state == 'completed' and any(o['state'] != 'completed' for o in operations):
                raise WorkflowError('run has unfinished operations')
            finished = _clock(self.clock())
            receipt = {**status['receipt'], 'reason': reason, 'finished_at': finished,
                       'elapsed_seconds': max(0, finished - status['started']),
                       'runtime_measure': 'wall_clock_including_interruptions'}
            self.store.db.execute('UPDATE runs SET state=?,receipt=? WHERE run_id=?',
                                  (state, canonical(receipt), run_id))
        return self.status(run_id)

    def unit(self, run_id, key, fingerprint, callback, *, reservation=None, provider=False,
             failure_usage=None, publication_records=None, failure_result=None):
        if type(provider) is not bool:
            raise WorkflowError('provider must be boolean')
        reservation = dict(reservation or {})
        reservation.setdefault('derived_bytes', 16384)
        for name, amount in reservation.items():
            if name not in asdict(Limits()) or name == 'runtime_seconds':
                raise WorkflowError('unknown reservation dimension')
            integer(amount, name)
        if provider:
            reservation['provider_calls'] = max(1, reservation.get('provider_calls', 0))
        with self.store.transaction():
            status = self.status(run_id)
            prior = next((o for o in status['operations'] if o['unit_key'] == key), None)
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise RevisionConflict('unit input fingerprint changed')
                if prior['state'] == 'completed':
                    return prior['result']
                if prior['state'] == 'failed' and prior['charge_status'] == 'not_applicable':
                    return {'status': 'failed', 'reason': 'new_attempt_identity_required'}
                # A crash might have happened after provider execution. The
                # reservation is not released, and the callback is not repeated.
                if prior['state'] in ('reserved', 'uncertain') or prior['charge_status'] != 'not_applicable':
                    self.store.db.execute("UPDATE operations SET state='uncertain',reason='receipt_missing' WHERE run_id=? AND unit_key=?", (run_id, key))
                    return {'status': 'ambiguous_operation', 'charge_status': prior['charge_status']}
            now = _clock(self.clock())
            if status['state'] in ('completed', 'failed', 'canceled'):
                raise WorkflowError('run is terminal')
            if now >= status['deadline']:
                self.store.db.execute("UPDATE runs SET state='partial',receipt=? WHERE run_id=?",
                    (canonical({**status['receipt'], 'reason': 'deadline_exhausted'}), run_id))
                return {'status': 'budget_exhausted', 'reason': 'deadline_exhausted'}
            if provider and status['provider_accounting']['unverified_operations']:
                raise WorkflowError('original provider admission is unverified; retain history '
                                    'and recover admission evidence before new provider work; '
                                    'do not reset the original run or deadline')
            limits = Limits.from_dict(status['plan'].get('limits', {}))
            for name, amount in reservation.items():
                if not prior and status['used'][name] + amount > getattr(limits, name):
                    self.store.db.execute("UPDATE runs SET state='partial',receipt=? WHERE run_id=?",
                        (canonical({**status['receipt'], 'reason': name + '_exhausted'}), run_id))
                    return {'status': 'budget_exhausted', 'reason': name + '_exhausted'}
            if prior:
                if prior['reservation'] != reservation:
                    raise RevisionConflict('unit reservation changed')
                self.store.db.execute("UPDATE operations SET state='reserved' WHERE run_id=? AND unit_key=?", (run_id, key))
            else:
                self.store.db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)',
                    (run_id, key, fingerprint, 'reserved', canonical(reservation), None,
                     'unknown' if provider else 'not_applicable', ''))
        try:
            result = callback()
            encoded = canonical(result)
            actual = dict(reservation)
            actual['derived_bytes'] = len(encoded)
            if isinstance(result, dict):
                for dimension, amount in result.get('_usage', {}).items():
                    integer(amount, dimension)
                    if dimension not in reservation:
                        raise BudgetExhausted('unreserved unit resource use')
                    if provider and dimension == 'provider_calls':
                        # Callback payloads cannot refund external admission.
                        # Successful output does not resolve billing ambiguity.
                        amount = max(amount, reservation[dimension])
                    actual[dimension] = amount + (len(encoded) if dimension == 'derived_bytes' else 0)
            # The callback's output is bounded before becoming a committed unit.
            if any(actual[name] > reservation[name] for name in actual):
                raise BudgetExhausted('unit output exceeds reserved resources')
            staged_digest = self.store.stage_artifact(encoded)
        except BaseException as exc:
            with self.store.transaction():
                # A local callback may account for resources actually consumed
                # before a known failure. External charges and crashes remain
                # reserved; lack of a receipt is never evidence of zero cost.
                if not provider and failure_usage is not None and isinstance(exc, Exception):
                    measured = failure_usage()
                    settled = dict(reservation)
                    for name, amount in measured.items():
                        integer(amount, name)
                        if name not in reservation or amount > reservation[name]:
                            raise BudgetExhausted('invalid local failure accounting')
                        settled[name] = amount
                    self.store.db.execute('UPDATE operations SET reservation=? WHERE run_id=? AND unit_key=?',
                        (canonical(settled), run_id, key))
                self.store.db.execute('UPDATE operations SET state=?,reason=? WHERE run_id=? AND unit_key=?',
                    ('uncertain' if provider else 'failed', type(exc).__name__, run_id, key))
                if not provider and failure_result is not None and isinstance(exc, Exception):
                    self.store.db.execute('UPDATE operations SET result=? WHERE run_id=? AND unit_key=?',
                        (canonical(failure_result()), run_id, key))
                self.store.db.execute("UPDATE runs SET state='partial' WHERE run_id=?", (run_id,))
            raise
        with self.store.transaction():
            artifact = self.store._bind_artifact(staged_digest, 'private_redacted', len(encoded))
            if publication_records is not None:
                self.store._import_records(publication_records())
            receipt = {'status': 'completed', 'artifact_ref': artifact}
            if provider:
                receipt['provider_admission'] = {'version': 1,
                                                 'reserved_calls': reservation['provider_calls']}
            self.store.db.execute("UPDATE operations SET state='completed',result=?,reservation=?,reason='' WHERE run_id=? AND unit_key=?",
                (canonical(receipt), canonical(actual), run_id, key))
        return receipt

    def record_preflight(self, run_id, record, *, captured_bytes, source_files, evidence_refs):
        """Settle already bounded local inventory; never execute more work.

        Inventory determines the frozen plan, so its receipt follows discovery.
        Unlike a new callback, settling observed reads must remain possible at
        the deadline. Plan publication and its receipt share a transaction.
        """
        integer(captured_bytes, 'captured bytes')
        integer(source_files, 'source files')
        payload = {'record_refs': [record.ref.to_dict()], 'evidence_refs': evidence_refs}
        encoded = canonical(payload)
        usage = {'captured_bytes': captured_bytes, 'source_files': source_files,
                 'derived_bytes': len(record.encoded) + len(encoded)}
        fingerprint = digest(record.encoded)
        with self.store.transaction():
            status = self.status(run_id)
            prior = next((o for o in status['operations'] if o['unit_key'] == 'planning'), None)
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise RevisionConflict('planning fingerprint changed')
                return prior['result']
            limits = Limits.from_dict(status['plan'].get('limits', {}))
            for dimension in ('captured_bytes', 'source_files'):
                if status['used'][dimension] + usage[dimension] > getattr(limits, dimension):
                    raise BudgetExhausted('inventory exceeded its read allowance')
            for ref in evidence_refs:
                self.store.read_artifact(ref)
            if status['used']['derived_bytes'] + usage['derived_bytes'] > limits.derived_bytes:
                usage['derived_bytes'] = 0
                result = {'status': 'failed', 'reason': 'planning_output_exceeds_budget',
                          'evidence_refs': evidence_refs}
                state = 'failed'
            else:
                sha = self.store.stage_artifact(encoded)
                ref = self.store._bind_artifact(sha, 'private_metadata', len(encoded))
                self.store._import_records([record])
                result = {'status': 'completed', 'artifact_ref': ref}
                state = 'completed'
            self.store.db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)',
                (run_id, 'planning', fingerprint, state, canonical(usage), canonical(result),
                 'not_applicable', result.get('reason', '')))
        return result

    def reconcile_charge(self, run_id, key, *, status, evidence_ref):
        if status not in ('billed', 'not_billed'):
            raise WorkflowError('unsupported charge status')
        self.store.read_artifact(evidence_ref)
        with self.store.transaction():
            updated = self.store.db.execute('UPDATE operations SET charge_status=?,reason=? WHERE run_id=? AND unit_key=?',
                (status, 'billing_evidence:' + evidence_ref['sha256'], run_id, key)).rowcount
            if not updated:
                raise WorkflowError('unknown operation')

    def publish_view(self, run_id, payload, *, cards=0):
        """Publish a bounded, pure receipt view without resetting the deadline.

        File staging is idempotent and has no evaluator/provider side effects.
        Artifact metadata and the completed view receipt become visible in the
        same transaction. An interrupted staging file is an unbound orphan.
        """
        encoded = canonical(payload) + b'\n'
        return self._publish_bytes(run_id, encoded, cards=cards, prefix='view:')

    def publish_render(self, run_id, data):
        """Account for a standalone rendered artifact without parsing it as JSON."""
        if not isinstance(data, bytes):
            raise WorkflowError('render must be bytes')
        return self._publish_bytes(run_id, data, cards=0, prefix='render:')

    def _publish_bytes(self, run_id, encoded, *, cards, prefix):
        integer(cards, 'cards')
        fingerprint = digest(encoded)
        key = prefix + fingerprint
        # Check before staging as well as under the transaction. The latter
        # catches competing publishers; rejected staged bytes remain unbound.
        status = self.status(run_id)
        limits = Limits.from_dict(status['plan'].get('limits', {}))
        prior = next((o for o in status['operations'] if o['unit_key'] == key), None)
        if prior:
            return prior['result']['artifact_ref']
        if status['used']['derived_bytes'] + len(encoded) > limits.derived_bytes:
            raise BudgetExhausted('receipt exceeds remaining derived allowance')
        sha = self.store.stage_artifact(encoded)
        with self.store.transaction():
            status = self.status(run_id)
            existing = next((o for o in status['operations'] if o['unit_key'] == key), None)
            if existing:
                return existing['result']['artifact_ref']
            if status['used']['derived_bytes'] + len(encoded) > limits.derived_bytes or status['used']['cards'] + cards > limits.cards:
                raise BudgetExhausted('receipt publication exceeds remaining derived/card allowance')
            reference = self.store._bind_artifact(sha, 'private_redacted', len(encoded))
            reservation = {'derived_bytes': len(encoded), 'cards': cards}
            self.store.db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)',
                (run_id, key, fingerprint, 'completed', canonical(reservation),
                 canonical({'status': 'completed', 'artifact_ref': reference}),
                 'not_applicable', 'receipt_publication'))
        return reference
