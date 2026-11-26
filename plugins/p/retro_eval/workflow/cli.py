"""Local workflow entry points. Evidence stays in an explicitly external store."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import time

from ..cli import command
from .importing import import_manifest
from .packets import make_packet
from .records import Record, RecordRef, canonical, strict_loads
from .registry import Registry, external_path
from .runner import Limits, Runner
from .service import run_automatic
from .sources import default_roots, resolve_source_path


def _root_default():
    base = os.environ.get('RETRO_HOME')
    return Path(base) / 'workflow' if base else Path.home() / '.local' / 'share' / 'retro' / 'workflow'


@command
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, default=_root_default())
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='Choose a bounded plan and mine authorized local history')
    run.add_argument('--source', action='append', default=[], metavar='NAME=ROOT')
    run.add_argument('--limits', type=Path, help='Optional bounded policy override')
    run.add_argument('--resume', help='Resume an existing run without resetting its deadline')
    run.add_argument('--pack', action='append', help='Optional enabled domain packs')
    inventory = commands.add_parser('inventory', help='Inspect bounded source metadata without capturing history or changing stored runs')
    inventory.add_argument('--source', action='append', default=[], metavar='NAME=ROOT')
    inventory.add_argument('--output', type=Path, required=True, help='New private JSON file outside the store and source roots')
    inventory.add_argument('--file-limit', type=int, default=1000)
    inventory.add_argument('--runtime-seconds', type=int, default=30)
    inventory.add_argument('--previous', type=Path, help='Optional private metadata snapshot to compare (at most 64 MiB)')
    fragment = commands.add_parser('fragment', help='Capture a private bounded byte range for discovery, without normalizing a session')
    fragment.add_argument('--source-root', type=Path, required=True)
    fragment.add_argument('--file', type=Path, required=True, help='File relative to the source root')
    fragment.add_argument('--output', type=Path, required=True, help='New external directory; parent must exist')
    fragment.add_argument('--max-bytes', type=int, default=1024 * 1024, help='At most 64 MiB; defaults to 1 MiB')
    selection = fragment.add_mutually_exclusive_group(required=True)
    selection.add_argument('--offset', type=int)
    selection.add_argument('--tail', action='store_true')
    commands.add_parser('status', help='Show durable run receipts and current applicability')
    export = commands.add_parser('export', help='Export a private portable bundle')
    export.add_argument('destination', type=Path)
    load = commands.add_parser('import', help='Import a private portable bundle')
    load.add_argument('bundle', type=Path)
    manifest = commands.add_parser('manifest', help='Import an explicit case manifest or corrections')
    manifest.add_argument('manifest', type=Path)
    effect = commands.add_parser('effect-import', help='Preserve an existing v1 effect record without upgrading its claims')
    effect.add_argument('--record', type=Path, required=True)
    effect.add_argument('--evidence-index', type=Path, required=True)
    commands.add_parser('packet', help='Render current delivery evidence as JSON')
    inspect = commands.add_parser('inspect', help='Prepare anchored candidates and noncandidate contrasts for the agent')
    inspect.add_argument('--run')
    inspect.add_argument('--candidate')
    inspect.add_argument('--limit', type=int, default=12)
    evidence = commands.add_parser('evidence', help='Capture one explicitly supplied external artifact without executing it')
    evidence.add_argument('file', type=Path)
    evidence.add_argument('--sha256', required=True)
    evidence.add_argument('--max-bytes', type=int, default=8 * 1024 * 1024)
    pilot = commands.add_parser('pilot', help='Prepare/compare the installed replay, or register/review an evidence-bound custom pilot')
    pilot.add_argument('action', choices=('prepare', 'compare', 'register', 'review'))
    pilot.add_argument('--labels', type=Path)
    pilot.add_argument('--spec', type=Path, help='External custom registration or follow-up JSON')
    action = commands.add_parser('disposition', help='Record a reversible local attention decision')
    action.add_argument('candidate_key')
    action.add_argument('state', choices=('active', 'snoozed', 'dismissed', 'provisional', 'adopted', 'retired'))
    action.add_argument('--reason', required=True)
    action.add_argument('--due-at')
    args = parser.parse_args(argv)
    if args.command == 'fragment':
        from .fragments import write_fragment
        result = write_fragment(args.source_root, args.file, args.output,
                                max_bytes=args.max_bytes, offset=args.offset, tail=args.tail,
                                protected_paths=(args.store,))
        print(canonical(result).decode())
        return 0 if result['covers_observed_file'] else 1
    if args.command in ('run', 'inventory'):
        roots = default_roots()
        if args.source:
            roots = {}
            for spec in args.source:
                name, sep, value = spec.partition('=')
                if not sep or not name or not value or name in roots:
                    raise ValueError('source must be a unique NAME=ROOT')
                roots[name] = Path(value)
    if args.command == 'run':
        limits = Limits.from_dict(strict_loads(args.limits.read_bytes())) if args.limits else Limits()
        result = run_automatic(args.store, roots=roots, limits=limits, resume=args.resume,
                               enabled_packs=args.pack or ('diagnosis', 'review', 'design'))
        # The stdout receipt is small; transcripts and candidate indexes stay in
        # the private report, never accidentally streamed as ordinary CLI output.
        print(canonical({k: result[k] for k in ('run_id', 'state', 'coverage', 'used', 'usage_scope',
            'report_path', 'next_action', 'exit_code')}).decode())
        return result['exit_code']
    if args.command == 'inventory':
        Limits(source_files=args.file_limit, runtime_seconds=args.runtime_seconds)
        if args.output.is_symlink():
            raise ValueError('inventory output must be a new file, not a symlink')
        destination = external_path(args.output)
        if destination.exists():
            raise ValueError('inventory output already exists')
        for protected in [args.store, *roots.values()]:
            protected = resolve_source_path(Path(protected).expanduser())
            if destination == protected or protected in destination.parents:
                raise ValueError('inventory output must be outside the store and source roots')
        previous = None
        if args.previous is not None:
            from .inventory import compare_inventory
            with external_path(args.previous).open('rb') as handle:
                data = handle.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError('previous inventory exceeds 64 MiB')
            previous = strict_loads(data)
            # Validate before enumerating sources or creating output.
            compare_inventory(previous, previous)
    store = Registry(args.store, readonly=args.command in ('status', 'export', 'packet', 'inventory'))
    try:
        if args.command == 'inventory':
            from .planning import inventory_sources
            observed_at = time.time()
            snapshot = inventory_sources(roots, store.id_salt, file_limit=args.file_limit,
                                         deadline=observed_at + args.runtime_seconds)
            snapshot['observed_at_epoch_seconds'] = observed_at
            snapshot['observation_limit'] = 'Metadata retrieval only; event freshness and task eligibility are not established.'
            if previous is not None:
                snapshot['comparison'] = compare_inventory(previous, snapshot)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
                handle.write(canonical(snapshot) + b'\n')
            result = {'inventory_path': str(destination), 'files': len(snapshot['files']),
                      'population_complete': snapshot['population_complete'], 'source_contents_captured': False,
                      'next_action': 'Inspect the private inventory and coverage gaps; metadata alone does not establish new eligible work.'}
            comparison_incomplete = False
            if previous is not None:
                comparison = snapshot['comparison']
                result['comparison'] = {k: len(comparison[k]) for k in (
                    'newly_observed', 'metadata_changed', 'not_observed_in_current',
                    'incomparable_sources', 'coverage_gaps')}
                result['comparison']['equal_metadata_count'] = comparison['equal_metadata_count']
                comparison_incomplete = bool(comparison['incomparable_sources'] or comparison['coverage_gaps'])
            print(canonical(result).decode())
            return 0 if snapshot['population_complete'] and not comparison_incomplete else 1
        elif args.command == 'status':
            with store.read_snapshot():
                runner = Runner(store)
                cases = store.heads(kind='case')
                result = {'runs': [{**{k: r[k] for k in ('run_id', 'state', 'started', 'deadline', 'used', 'usage_scope', 'receipt', 'provider_accounting')},
                                    **({'invocation_ref': r['plan']['invocation_ref']}
                                       if 'invocation_ref' in r['plan'] else {})}
                                   for r in runner.list_runs()],
                          'current_applicability': store.applicability([c.ref for c in cases])}
                for run in result['runs']:
                    run['result_applicability'] = runner.result_applicability(run['run_id'])
                analysis = store.heads(kind='analysis')
                result['analysis_applicability'] = store.applicability([r.ref for r in analysis])
                from .pilot_followup import followups
                result['pilot_followups'] = followups(store)
                from .recurring import followups as recurring_followups
                result['recurring_followups'] = recurring_followups(store)
                from .legacy import effect_summaries
                result['legacy_effects'] = effect_summaries(store)
                result['current_applicability']['current'] = (
                    result['current_applicability']['current'] and
                    result['analysis_applicability']['current'] and
                    all(e['current_applicability']['current'] for e in result['legacy_effects']) and
                    all(p['current_applicability']['current'] for p in result['pilot_followups']) and
                    all(r['current_applicability']['current'] for r in result['recurring_followups']) and
                    all(r['result_applicability']['current'] for r in result['runs']))
        elif args.command == 'export':
            destination = external_path(args.destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = canonical(store.export_bundle()) + b'\n'
            # Refuse accidental overwrite; export is a new private artifact.
            with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
                handle.write(data)
            result = {'exported': str(destination), 'bytes': len(data), 'private': True}
        elif args.command == 'import':
            refs = store.import_bundle(strict_loads(args.bundle.read_bytes()))
            result = {'imported_records': len(refs)}
        elif args.command == 'manifest':
            refs = import_manifest(args.manifest.read_bytes(), store)
            result = {'imported_records': len(refs)}
        elif args.command == 'effect-import':
            from .legacy import import_effect
            record = import_effect(store, args.record, args.evidence_index)
            result = {'decision_ref': record.ref.to_dict(),
                      'benefit_status': record.payload['benefit_status'],
                      'binding_check': record.payload['binding_check']}
        elif args.command == 'inspect':
            from .inspection import inspect_run
            result = inspect_run(store, run_id=args.run, candidate_key=args.candidate, limit=args.limit)
        elif args.command == 'evidence':
            from .pilot_followup import capture_evidence
            result = capture_evidence(store, args.file, args.sha256, max_bytes=args.max_bytes)
        elif args.command == 'disposition':
            from .recurring import disposition
            record = disposition(store, args.candidate_key, args.state,
                                 reason=args.reason, due_at=args.due_at)
            result = {'decision_ref': record.ref.to_dict(), 'global_workflow_changed': False}
        elif args.command == 'pilot':
            from .pilots import prepare, compare
            if args.action in ('register', 'review'):
                from .pilot_followup import register, review
                if args.spec is None or args.labels is not None:
                    raise ValueError('custom pilot action requires --spec and does not accept --labels')
                spec = strict_loads(external_path(args.spec).read_bytes())
                result = (register if args.action == 'register' else review)(store, spec)
            elif args.spec is not None:
                raise ValueError('--spec is only for custom pilot register/review')
            elif args.action == 'prepare':
                result = prepare(store)
            else:
                if args.labels is None:
                    raise ValueError('compare requires the completed blind-label packet')
                labels = strict_loads(args.labels.read_bytes())
                result = compare(store, RecordRef.from_dict(labels['pilot_ref']), labels)
        else:
            with store.read_snapshot():
                result = make_packet(store, store.heads(kind='case'))
        print(canonical(result).decode())
        return 0
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
