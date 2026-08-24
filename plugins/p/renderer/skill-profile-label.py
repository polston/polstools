#!/usr/bin/env python3
"""Fail-soft Claude status label backed by the shared activation resolver."""

import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))

try:
    import skill_activation

    try:
        data = json.load(sys.stdin)
        session_id = data.get("session_id") if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError, UnicodeError, ValueError):
        session_id = None
    policy_path = HERE / "skill-activation-v1.json"
    if not policy_path.is_file():
        policy_path = HERE.parent / "profiles" / "skill-activation-v1.json"
    policy = skill_activation.load_manifest(policy_path)
    resolved = skill_activation.resolve(policy, session_id=session_id)
    print(skill_activation.label(resolved))
except Exception:
    print("p:?")
