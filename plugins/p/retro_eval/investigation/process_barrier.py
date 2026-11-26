"""POSIX owned-group barrier for cooperative local effect writers.

All effect writers must remain in the owned group. This is not containment for
adversarial children, detached sessions, remote services or a killed controller.
A live anchor retains the group identity until cleanup; never signal a PID from
an untrusted worker's report. Receipts attest observations, not remote cancellation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time

VERSION = 2
MODEL = 'posix-cooperative-owned-group-v1'
# The anchor deliberately survives target exit, retaining the group identifier.
_ANCHOR = r'''
import json,os,subprocess,sys,time
from pathlib import Path
marker=Path(sys.argv[1])
def save(value):
    tmp=marker.with_suffix('.tmp');tmp.write_text(json.dumps(value));os.replace(tmp,marker)
try:
    child=subprocess.Popen(sys.argv[2:])
    save({'phase':'running','pid':child.pid})
    code=child.wait()
    save({'phase':'exited','pid':child.pid,'returncode':code})
except BaseException as exc:
    save({'phase':'launch_failed','error':type(exc).__name__+': '+str(exc)})
while True: time.sleep(1)
'''


def group_states(pgid):
    completed = subprocess.run(['ps', '-axo', 'pid=,pgid=,stat='],
                               capture_output=True, text=True, timeout=5)
    if completed.returncode:
        raise RuntimeError('process observation failed: ' + completed.stderr.strip())
    states = {}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3:
            raise RuntimeError('malformed process observation')
        pid, group = int(parts[0]), int(parts[1])
        if group == pgid:
            states[str(pid)] = parts[2]
    return states


def _owner_id(value):
    return (isinstance(value, str) and len(value) == 32
            and all(c in '0123456789abcdef' for c in value))


def _states(value):
    return (isinstance(value, dict) and all(
        isinstance(pid, str) and pid.isascii() and pid.isdecimal() and bool(pid.strip('0'))
        and isinstance(state, str) and state and state[0] in 'RSDTtIZUWXP'
        for pid, state in value.items()))


def successor_allowed(receipt, *, expected_owner=None):
    """Validate cleanup for the caller's independently retained owner identity.

    A receipt from another attempt, a legacy unbound receipt, or contradictory
    evidence cannot authorize recovery. Never derive expected_owner from the
    receipt being checked. This is consistency checking of trusted controller
    evidence, not authentication or proof of remote/escaped-writer quiescence.
    """
    if not (_owner_id(expected_owner) and isinstance(receipt, dict)
            and receipt.get('owner_id') == expected_owner
            and type(receipt.get('version')) is int and receipt['version'] == VERSION
            and receipt.get('model') == MODEL and receipt.get('verified') is True
            and receipt.get('ownership_confirmed') is True
            and receipt.get('running_after') == {} and receipt.get('errors') == []
            and receipt.get('launch_succeeded') is True):
        return False
    group, before, after, target = (receipt.get(k) for k in
                                    ('group', 'before', 'after', 'target'))
    if not (type(group) is int and group > 0 and _states(before) and _states(after)
            and str(group) in before and not before[str(group)].startswith('Z')
            and all(state.startswith('Z') for state in after.values())
            and isinstance(target, dict) and type(target.get('pid')) is int
            and target['pid'] > 0):
        return False
    return ((target.get('phase') == 'running' and 'returncode' not in target) or
            (target.get('phase') == 'exited' and type(target.get('returncode')) is int))


class OwnedProcess:
    """Start, observe and always clean a command in a fresh anchored group.

    Use as a context manager or call cleanup in finally. stdout/stderr/stdin are
    file handles (not PIPE), so orphan writers cannot deadlock communicate().
    The control directory must be fresh and unavailable for worker mutation.
    Caller must establish that effect writers obey MODEL before scoring a run.
    """
    def __init__(self, command, control_dir, *, cwd=None, env=None,
                 stdin=None, stdout=None, stderr=None):
        if os.name != 'posix':
            raise RuntimeError('owned process groups require POSIX')
        if any(stream == subprocess.PIPE for stream in (stdin, stdout, stderr)):
            raise ValueError('use files, not PIPE, for owned process streams')
        self.command = list(command)
        self.owner_id = secrets.token_hex(16)
        self.directory = Path(control_dir)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.marker = self.directory / 'target.json'
        self.options = dict(cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr)
        self.proc = None
        self.receipt = None

    def start(self):
        if self.proc is not None or self.receipt is not None:
            raise RuntimeError('owned command cannot be launched twice')
        try:
            self.proc = subprocess.Popen([sys.executable, '-c', _ANCHOR,
                                          str(self.marker), *self.command],
                                         start_new_session=True, **self.options)
        except BaseException:
            self.cleanup(reason='anchor_launch_failure')
            raise
        return self

    def observe(self):
        if not self.marker.exists():
            return {'phase': 'starting'}
        value = json.loads(self.marker.read_text())
        if not isinstance(value, dict) or value.get('phase') not in ('running', 'exited', 'launch_failed'):
            raise RuntimeError('invalid target observation')
        return value

    def wait(self, *, timeout, stop_when=None):
        deadline = time.monotonic() + timeout
        while True:
            observed = self.observe()
            if observed['phase'] in {'exited', 'launch_failed'}:
                return observed
            if self.proc.poll() is not None:
                raise RuntimeError('anchor exited unexpectedly; ownership is unverified')
            if stop_when and stop_when():
                return {'phase': 'interrupted'}
            if time.monotonic() >= deadline:
                return {'phase': 'timeout'}
            time.sleep(.03)

    def cleanup(self, *, reason='context_exit', timeout=5):
        if self.receipt is not None:
            return self.receipt
        r = {'version': VERSION, 'model': MODEL, 'owner_id': self.owner_id, 'reason': reason,
             'ownership_confirmed': False, 'launch_succeeded': False,
             'verified': False, 'running_after': None, 'errors': [],
             'scope': 'local cooperative writers in the anchored process group'}
        self.receipt = r
        if self.proc is None:
            r['errors'].append('anchor was not launched')
            return self._save()
        r['group'] = self.proc.pid
        try:
            try:
                observed = self.observe()
                r['target'] = observed
                r['launch_succeeded'] = observed['phase'] in {'running', 'exited'}
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                r['errors'].append('target observation: ' + str(exc))
            # This object is the only waiter. A live unreaped direct child owns
            # its PID until our kill; a failed observer must not skip cleanup.
            if self.proc.poll() is not None:
                raise RuntimeError('live ownership anchor unavailable')
            if os.getpgid(self.proc.pid) != self.proc.pid:
                raise RuntimeError('ownership anchor changed process group')
            try:
                r['before'] = group_states(self.proc.pid)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                r['errors'].append('before observation: ' + str(exc))
            r['ownership_confirmed'] = True
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=timeout)
            deadline = time.monotonic() + timeout
            while True:
                states = group_states(self.proc.pid)
                running = {pid: state for pid, state in states.items()
                           if not state.startswith('Z')}
                r['after'] = states
                r['running_after'] = running
                if not running or time.monotonic() >= deadline:
                    break
                time.sleep(.03)
            r['verified'] = not running and not r['errors']
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            r['errors'].append(type(exc).__name__ + ': ' + str(exc))
        return self._save()

    def _save(self):
        (self.directory / 'cleanup.json').write_text(json.dumps(self.receipt, indent=2) + '\n')
        return self.receipt

    def __enter__(self):
        return self.start()

    def __exit__(self, kind, value, traceback):
        self.cleanup(reason='controller_exception' if kind else 'context_exit')
        return False
