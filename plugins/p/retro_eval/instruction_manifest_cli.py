"""CLI for private instruction-source provenance manifests."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .instruction_manifest import (InstructionSource, hash_instruction_source,
                                   write_instruction_manifest)


def _mapping(values, label):
    result = {}
    for value in values:
        try:
            key, item = value.split("=", 1)
        except ValueError as exc:
            raise ValueError("%s values must use kind=value" % label) from exc
        if not key or not item or key in result:
            raise ValueError("%s values require unique non-empty kinds" % label)
        result[key] = item
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    writer = commands.add_parser("write")
    writer.add_argument("--output", type=Path, required=True)
    writer.add_argument("--activated-at", required=True)
    writer.add_argument("--source", action="append", required=True,
                        metavar="KIND=PATH")
    writer.add_argument("--version", action="append", default=[],
                        metavar="KIND=VERSION")
    writer.add_argument("--commit", action="append", default=[],
                        metavar="KIND=COMMIT")
    writer.add_argument("--privacy-class", action="append", default=[],
                        metavar="KIND=CLASS")
    args = parser.parse_args(argv)

    sources = _mapping(args.source, "source")
    versions = _mapping(args.version, "version")
    commits = _mapping(args.commit, "commit")
    privacy = _mapping(args.privacy_class, "privacy class")
    unknown = (set(versions) | set(commits) | set(privacy)) - set(sources)
    if unknown:
        raise ValueError("source metadata references an unknown kind")
    try:
        activated_at = datetime.fromisoformat(
            args.activated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("activated-at must be an ISO-8601 timestamp") from exc
    manifest = write_instruction_manifest(
        args.output,
        tuple(InstructionSource(
            source_kind=kind,
            content_sha256=hash_instruction_source(Path(path)),
            version=versions.get(kind, ""),
            commit=commits.get(kind, ""),
            privacy_class=privacy.get(kind, "private_local"),
        ) for kind, path in sorted(sources.items())),
        activated_at=activated_at,
    )
    print(json.dumps({
        "manifest_sha256": manifest.manifest_sha256,
        "sources": len(manifest.sources),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
