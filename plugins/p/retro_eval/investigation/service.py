"""Transactional investigation operations; native harnesses execute the work."""
from __future__ import annotations

import time
from ..workflow.errors import RevisionConflict, WorkflowError
from ..workflow.records import Record, RecordRef, canonical, integer
from ..workflow.investigation_records import contract_digest
from ..workflow.investigation_state import apply, audit_state, projection_key, rebuild


class Investigation:
    def __init__(self, store, *, clock=None):
        self.store = store
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)

    def begin(self, key, frame, *, deadline_ms, limits, authority, capabilities,
              study_scope='investigations'):
        started = self.clock()
        record = Record.create('investigation', key, {
            'version': 1, 'policy_sha256': contract_digest(), 'frame': frame,
            'started_ms': started, 'deadline_ms': deadline_ms, 'limits': limits,
            'authority': authority, 'capabilities': capabilities},
            study_scope=study_scope, producer='investigation', producer_version='1')
        self.store.append(record, None)
        return record.ref

    def checkpoint(self, root):
        # Derive from authoritative history; never trust imported/cache metadata.
        self.store.db.execute('BEGIN')
        try:
            state = rebuild(self.store, root)
            audit = audit_state(self.store, state)
            # Keep the original result intact beside its effective interpretation.
            for view in state['interpretations'].values():
                view['applicability'] = self.store._applicability([RecordRef.from_dict(view['current_ref'])])
                action = state['actions'][view['action_id']]
                action['current_interpretation'] = view
            return {**state, 'audit': audit,
                    'completion_current': state['status'] == 'complete' and audit['ready'],
                    'remaining_ms': max(0, state['grants']['root']['deadline_ms'] - self.clock())}
        finally:
            self.store.db.rollback()

    def record(self, root, expected_sequence, event_type, data):
        integer(expected_sequence, 'expected sequence')
        with self.store.transaction():
            state = rebuild(self.store, root)
            if state['sequence'] != expected_sequence:
                raise RevisionConflict('investigation sequence changed; recover before retrying')
            if self.store.get(root).payload['policy_sha256'] != contract_digest():
                raise WorkflowError('investigation policy version differs; explicit migration required')
            event = Record.create('investigation_event',
                projection_key(root).rsplit(':', 1)[1] + ':' + str(expected_sequence + 1), {
                    'version': 1, 'root_ref': root.to_dict(),
                    'sequence': expected_sequence + 1, 'previous_sha256': state['tip_sha256'],
                    'at_ms': self.clock(), 'event_type': event_type, 'data': data},
                study_scope=root.study_scope, producer='investigation', producer_version='1')
            apply(self.store, state, event)
            self.store._append(event, None)
            self.store._link(event)
            self.store.db.execute('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                  (projection_key(root), canonical(state)))
        return event

    def admit(self, root, expected_sequence, action):
        # Identity is an idempotency key, not authorization to rerun the action.
        state = self.checkpoint(root)
        prior = state['actions'].get(action.get('id'))
        if prior is not None:
            original = {k: prior[k] for k in action if k in prior}
            if canonical(original) != canonical(action) or set(action) != set(prior) - {'state', 'frame_seq', 'result', 'current_interpretation'}:
                raise RevisionConflict('action identity already has different inputs')
            return {'admitted': False, 'reason': 'already_admitted', 'action': prior}
        event = self.record(root, expected_sequence, 'action', action)
        return {'admitted': True, 'event_ref': event.ref.to_dict(),
                'action_id': action['id'], 'dispatch': 'native harness; admission is not execution'}

    def settle(self, root, expected_sequence, result):
        state = self.checkpoint(root)
        prior = state['actions'].get(result.get('action_id'))
        if prior and prior.get('result') == result:
            return {'settled': False, 'reason': 'already_recorded'}
        event = self.record(root, expected_sequence, 'result', result)
        return {'settled': True, 'event_ref': event.ref.to_dict()}

    def audit(self, root):
        return self.checkpoint(root)['audit']
