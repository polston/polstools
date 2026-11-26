"""Trusted, versioned method composition without executable plugin callbacks."""
from pathlib import Path

from ..workflow.errors import WorkflowError
from ..workflow.records import canonical, digest, strict_loads, text
from ..workflow.investigation_records import fields, texts, contract


class MethodRegistry:
    def __init__(self, manifest=None, *, plugin_root=None):
        self.root = Path(plugin_root or Path(__file__).resolve().parents[2]).resolve()
        manifest = manifest or self.root / 'profiles' / 'investigation-methods-v1.json'
        value = strict_loads(Path(manifest).read_bytes())
        fields(value, ('version', 'modules'), 'method registry')
        if type(value['version']) is not int or value['version'] != 1 or not isinstance(value['modules'], list):
            raise WorkflowError('unsupported method registry')
        self.modules = {}
        for module in value['modules']:
            fields(module, ('id', 'version', 'policy_versions', 'domains', 'requires',
                            'capabilities', 'incompatible', 'claim_types', 'obligations',
                            'guidance', 'methods'), 'method module')
            for k in ('id', 'version', 'guidance'):
                text(module[k], k)
            if module['id'] in self.modules:
                raise WorkflowError('duplicate module identity')
            for k in ('domains', 'requires', 'capabilities', 'incompatible', 'claim_types', 'obligations'):
                texts(module[k], k)
            if not isinstance(module['policy_versions'], list) or not module['policy_versions'] or any(type(v) is not int or v < 1 for v in module['policy_versions']):
                raise WorkflowError('invalid policy compatibility')
            if set(module['claim_types']) - set(contract()['claim_types']):
                raise WorkflowError('invalid module claim type')
            path = (self.root / module['guidance']).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise WorkflowError('guidance must be a trusted file inside the plugin root')
            if not isinstance(module['methods'], list) or not module['methods']:
                raise WorkflowError('module needs methods')
            ids = set()
            for method in module['methods']:
                fields(method, ('id', 'prerequisites', 'outputs', 'verification', 'failure_modes', 'alternatives'), 'method')
                text(method['id'], 'method identity')
                if method['id'] in ids:
                    raise WorkflowError('duplicate method identity')
                ids.add(method['id'])
                for k in ('prerequisites', 'outputs', 'verification', 'failure_modes', 'alternatives'):
                    texts(method[k], k)
                    if not method[k]:
                        raise WorkflowError('method ' + k + ' cannot be empty')
            self.modules[module['id']] = {**module, 'guidance_sha256': digest(path.read_bytes())}
        if 'general' not in self.modules:
            raise WorkflowError('general fallback module is required')

    def resolve(self, domains, capabilities=()):
        texts(domains, 'observed domains')
        selected, deferred, visiting = {}, {}, set()

        def include(key, reason):
            if key in visiting:
                raise WorkflowError('method prerequisite cycle')
            if key in selected:
                return True
            if key in deferred:
                return False
            module = self.modules.get(key)
            if module is None:
                deferred[key] = 'missing prerequisite module'
                return False
            if 1 not in module['policy_versions']:
                deferred[key] = 'incompatible canonical policy version'
                return False
            missing = set(module['capabilities']) - set(capabilities)
            if missing:
                deferred[key] = 'missing capabilities: ' + ', '.join(sorted(missing))
                return False
            visiting.add(key)
            ready = all([include(dep, 'required by ' + key) for dep in module['requires']])
            visiting.remove(key)
            if not ready:
                deferred[key] = 'unavailable prerequisite module'
                return False
            selected[key] = {**module, 'reason': reason}
            return True

        include('general', 'fallback and shared obligations')
        for key, module in self.modules.items():
            matching = set(domains) & set(module['domains'])
            if matching:
                include(key, 'observed domains: ' + ', '.join(sorted(matching)))
        conflicts = sorted({tuple(sorted((key, other))) for key, m in selected.items()
                            for other in m['incompatible'] if other in selected})
        unknown = sorted(set(domains) - {d for m in self.modules.values() for d in m['domains']})
        result = {'selected': [selected[k] for k in sorted(selected)],
                  'deferred': deferred, 'conflicts': [list(c) for c in conflicts],
                  'unfamiliar_domains': unknown,
                  'next_action': 'resolve method conflicts' if conflicts else
                    'research domain-specific methods and limit expertise claims' if unknown else
                    'load only guidance relevant to the next method',
                  'obligations': sorted({x for m in selected.values() for x in m['obligations']}),
                  'authority': 'modules cannot grant permissions or relax canonical policy'}
        result['composition_sha256'] = digest(canonical(result))
        return result
