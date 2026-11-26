"""JSON transport for native-agent investigation state and evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import sqlite3

from ..workflow.errors import WorkflowError
from ..workflow.records import RecordRef, strict_loads
from ..workflow.registry import Registry
from ..workflow.investigation_records import (contract, contract_digest, correction_contract,
                                              correction_digest, event_specs)
from .service import Investigation


def read_input(path, *, bundle=False):
    with Path(path).open('rb') as handle:
        raw = handle.read() if bundle else handle.read(1_048_577)
    if not bundle and len(raw) > 1_048_576:
        raise WorkflowError('structured input exceeds one MiB; publish large evidence separately')
    value = strict_loads(raw)
    if not isinstance(value, dict):
        raise WorkflowError('structured input must be an object')
    return value


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('contract', help='show canonical policy and event fields')
    methods = sub.add_parser('methods', help='select trusted method guidance from observed domains')
    methods.add_argument('--domain', action='append', default=[])
    methods.add_argument('--capability', action='append', default=[])
    for command in ('begin', 'record', 'admit', 'settle', 'checkpoint', 'audit', 'artifact', 'export', 'import', 'collect-workflow', 'inspect-workflow'):
        q = sub.add_parser(command)
        q.add_argument('--store', required=True)
        if command not in ('artifact', 'export', 'import'):
            q.add_argument('--root', required=True, help='investigation identity')
            q.add_argument('--scope', default='investigations')
        if command in ('begin', 'record', 'admit', 'settle', 'artifact', 'import'):
            q.add_argument('--input', required=True, help='input file; raw bytes for artifact, JSON otherwise')
        if command in ('record', 'admit', 'settle', 'collect-workflow'):
            q.add_argument('--expected-sequence', type=int, required=True)
        if command in ('collect-workflow', 'inspect-workflow'):
            q.add_argument('--action-id', required=True)
        if command == 'record':
            q.add_argument('--type', required=True, choices=tuple(event_specs()))
        if command == 'artifact':
            q.add_argument('--content-class', choices=('public_synthetic', 'private_metadata', 'private_redacted', 'private_raw'), default='private_raw')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command == 'contract':
            result = {'policy_sha256': contract_digest(), 'contract': contract(),
                      'extensions': {'correction': {'sha256': correction_digest(),
                                                    'contract': correction_contract()}}}
        elif args.command == 'methods':
            from .methods import MethodRegistry
            result = MethodRegistry().resolve(args.domain, args.capability)
        else:
            store = Registry(Path(args.store), readonly=args.command in ('checkpoint', 'audit', 'export', 'inspect-workflow'))
            api = Investigation(store)
            root = RecordRef(args.scope, 'investigation', args.root, 1) if hasattr(args, 'root') else None
            data = read_input(args.input, bundle=args.command == 'import') if args.command in ('begin', 'record', 'admit', 'settle', 'import') else None
            if args.command == 'begin':
                required = {'frame', 'deadline_ms', 'limits', 'authority', 'capabilities'}
                if set(data) != required:
                    raise WorkflowError('begin input fields mismatch')
                result = api.begin(args.root, study_scope=args.scope, **data).to_dict()
            elif args.command == 'record':
                result = api.record(root, args.expected_sequence, args.type, data).to_dict()
            elif args.command == 'admit':
                result = api.admit(root, args.expected_sequence, data)
            elif args.command == 'settle':
                result = api.settle(root, args.expected_sequence, data)
            elif args.command in ('checkpoint', 'audit'):
                result = getattr(api, args.command)(root)
            elif args.command == 'collect-workflow':
                from .workflow import collect_workflow
                result = collect_workflow(api, root, args.expected_sequence, args.action_id)
            elif args.command == 'inspect-workflow':
                from .workflow import inspect_workflow
                result = inspect_workflow(api, root, args.action_id)
            elif args.command == 'artifact':
                result = store.publish_artifact(Path(args.input).read_bytes(), args.content_class)
            elif args.command == 'export':
                result = store.export_bundle()
            elif args.command == 'import':
                result = {'imported': [r.to_dict() for r in store.import_bundle(data)]}
            else:
                raise WorkflowError('unsupported command')
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        if args.command == 'audit' and not result['ready']:
            return 1
        return 0
    except (WorkflowError, OSError, ValueError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({'error': getattr(exc, 'code', 'invalid_input'), 'message': str(exc)}), file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
