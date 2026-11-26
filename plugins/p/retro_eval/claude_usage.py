"""Group captured Claude usage snapshots without discarding message content.

This is transcript-local accounting, not billing or cross-transcript request
deduplication. Anonymous messages have no reliable identity and remain separate.
SDK stream output placeholders are not settled transcript output measurements.
"""
from __future__ import annotations


class ClaudeMessageUsage:
    def __init__(self):
        self._identified = {}
        self._anonymous = []
        self.snapshot_count = 0

    def add(self, message):
        self.snapshot_count += 1
        identity = message.get('id')
        usage = message.get('usage')
        if isinstance(identity, str) and identity:
            self._identified[identity] = usage
        else:
            self._anonymous.append(usage)

    @property
    def usages(self):
        return tuple(self._identified.values()) + tuple(self._anonymous)

    @property
    def anonymous_count(self):
        return len(self._anonymous)
