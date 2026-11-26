"""Content identities for the installed local measurement composition."""
from pathlib import Path

from .records import digest


def measurement_composition():
    root = Path(__file__).resolve().parents[2]
    paths = [*sorted((root / 'retro_eval/workflow').glob('*.py')),
             *sorted((root / 'retro_eval/adapters').glob('*.py')),
             root / 'retro_eval/text_rules.py', root / 'retro_eval/taxonomies.py',
             root / 'retro_eval/claude_usage.py',
             root / 'retro_eval/schema.py', root / 'retro_eval/proposal_report.py',
             root / 'profiles/sources.json', root / 'profiles/tool-taxonomy.json']
    return {path.relative_to(root).as_posix(): digest(path.read_bytes()) for path in paths}
