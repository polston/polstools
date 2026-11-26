"""Shared investigation wire contracts; no dependency on its execution package."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

from .errors import UnsupportedVersion, WorkflowError
from .records import RecordRef, canonical, digest, integer, strict_loads, text


@lru_cache(maxsize=1)
def _contract_bytes():
    path = Path(__file__).resolve().parents[2] / 'skills' / 'investigating-and-improving' / 'contract-v1.json'
    return canonical(strict_loads(path.read_bytes()))


def contract():
    return strict_loads(_contract_bytes())


def contract_digest():
    return digest(_contract_bytes())


@lru_cache(maxsize=1)
def _correction_bytes():
    path = Path(__file__).resolve().parents[2] / 'skills' / 'investigating-and-improving' / 'correction-v1.json'
    return canonical(strict_loads(path.read_bytes()))


def correction_contract():
    return strict_loads(_correction_bytes())


def correction_digest():
    return digest(_correction_bytes())


def event_specs():
    return {**contract()['events'], 'correction': correction_contract()['event']}


def fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names):
        raise WorkflowError(label + ' fields mismatch')


def texts(value, label):
    if not isinstance(value, list):
        raise WorkflowError(label + ' must be a list')
    for item in value:
        text(item, label)
    if len(set(value)) != len(value):
        raise WorkflowError(label + ' contains duplicates')


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise WorkflowError('invalid investigation digest')


def evidence(value):
    if not isinstance(value, list):
        raise WorkflowError('evidence must be a list')
    for item in value:
        if not isinstance(item, dict):
            raise WorkflowError('invalid evidence reference')
        if set(item) == {'study_scope', 'kind', 'key', 'revision'}:
            RecordRef.from_dict(item)
        else:
            fields(item, ('sha256', 'content_class', 'selector'), 'artifact reference')
            sha(item['sha256'])
            from .records import CONTENT_CLASSES
            if not isinstance(item['content_class'], str) or item['content_class'] not in CONTENT_CLASSES:
                raise WorkflowError('invalid evidence content class')
            selector = item['selector']
            if not isinstance(selector, dict) or not isinstance(selector.get('kind'), str):
                raise WorkflowError('evidence selector required')
            kind = selector['kind']
            if kind == 'whole':
                fields(selector, ('kind',), 'whole selector')
            elif kind == 'json_pointer':
                fields(selector, ('kind', 'pointer'), 'JSON pointer selector')
                pointer = selector['pointer']
                if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
                    raise WorkflowError('invalid JSON pointer')
            elif kind == 'normalized_span':
                fields(selector, ('kind', 'span_id'), 'span selector')
                text(selector['span_id'], 'span identity')
            elif kind == 'jsonl_pointer':
                fields(selector, ('kind', 'line', 'pointer'), 'JSONL selector')
                integer(selector['line'], 'JSONL line', 1)
                pointer = selector['pointer']
                if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
                    raise WorkflowError('invalid JSON pointer')
            else:
                raise WorkflowError('unsupported evidence selector')


def resources(value):
    if not isinstance(value, dict) or set(value) - set(contract()['resource_units']):
        raise WorkflowError('unknown resource unit')
    for key, amount in value.items():
        integer(amount, key)


def frame(value):
    fields(value, ('subject', 'objective', 'requester', 'constraints',
                   'preferences', 'criteria', 'inputs'), 'frame')
    for key in ('subject', 'objective', 'requester'):
        text(value[key], key)
    for key in ('constraints', 'preferences'):
        texts(value[key], key)
    evidence(value['inputs'])
    criteria = value['criteria']
    if not isinstance(criteria, list) or not criteria:
        raise WorkflowError('frame needs acceptance criteria')
    keys = set()
    for item in criteria:
        fields(item, ('key', 'description', 'claim_type', 'verification_method', 'required'), 'criterion')
        for key in ('key', 'description', 'verification_method'):
            text(item[key], key)
        if item['key'] in keys or item['claim_type'] not in contract()['claim_types']:
            raise WorkflowError('duplicate criterion or invalid claim type')
        keys.add(item['key'])
        if type(item['required']) is not bool:
            raise WorkflowError('required must be boolean')
    if not any(item['required'] for item in criteria):
        raise WorkflowError('frame needs required acceptance')


def validate(kind, payload):
    if payload.get('version') != 1 or type(payload.get('version')) is not int:
        raise UnsupportedVersion('unsupported investigation contract')
    if kind == 'investigation':
        fields(payload, ('version', 'policy_sha256', 'frame', 'started_ms',
                         'deadline_ms', 'limits', 'authority', 'capabilities'), 'investigation')
        sha(payload['policy_sha256'])
        frame(payload['frame'])
        for name in ('started_ms', 'deadline_ms'):
            integer(payload[name], name)
        if payload['deadline_ms'] <= payload['started_ms']:
            raise WorkflowError('deadline must follow activation')
        resources(payload['limits'])
        texts(payload['authority'], 'authority')
        capabilities = payload['capabilities']
        if not isinstance(capabilities, dict) or not capabilities:
            raise WorkflowError('capabilities must be a nonempty object mapping names to {mode, scope}')
        for key, item in capabilities.items():
            text(key, 'capability')
            fields(item, ('mode', 'scope'), 'capability (required fields: mode, scope)')
            if item['mode'] not in contract()['enforcement']:
                raise WorkflowError('invalid enforcement level')
            text(item['scope'], 'capability scope')
        return
    fields(payload, ('version', 'root_ref', 'sequence', 'previous_sha256',
                     'at_ms', 'event_type', 'data'), 'investigation event')
    root = RecordRef.from_dict(payload['root_ref'])
    if root.kind != 'investigation' or root.revision != 1:
        raise WorkflowError('event requires immutable investigation root')
    integer(payload['sequence'], 'sequence', 1)
    integer(payload['at_ms'], 'event timestamp')
    if payload['sequence'] == 1:
        if payload['previous_sha256'] is not None:
            raise WorkflowError('first event cannot have a predecessor')
    else:
        sha(payload['previous_sha256'])
    text(payload['event_type'], 'event type')
    spec = event_specs().get(payload['event_type'])
    if spec is None:
        raise UnsupportedVersion('unsupported investigation event')
    data = payload['data']
    fields(data, spec, 'event data')
    for key, typ in spec.items():
        value = data[key]
        if typ == 'text':
            text(value, key)
        elif typ == 'string':
            if not isinstance(value, str):
                raise WorkflowError(key + ' must be a string')
        elif typ == 'texts':
            texts(value, key)
        elif typ == 'uint':
            integer(value, key)
        elif typ == 'bool':
            if type(value) is not bool:
                raise WorkflowError(key + ' must be boolean')
        elif typ == 'claim':
            if value not in contract()['claim_types']:
                raise WorkflowError('invalid claim type')
        elif typ == 'record_ref':
            RecordRef.from_dict(value)
        elif typ.startswith('enum:'):
            if value not in typ[5:].split(','):
                raise WorkflowError('invalid ' + key)
        else:
            {'evidence': evidence, 'resources': resources, 'frame': frame}[typ](value)
    if payload['event_type'] == 'correction' and data['contract_sha256'] != correction_digest():
        raise UnsupportedVersion('unsupported correction contract digest')
