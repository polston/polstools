"""Private, versioned instruction-source manifests.

The manifest contains only generic source metadata and content hashes. It must
remain outside repositories; evaluation artifacts bind to it by SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1


def _is_sha256(value: str) -> bool:
    return (len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _external(path: Path) -> None:
    resolved = path.resolve()
    if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
        raise ValueError("instruction manifests must remain outside repositories")


def _stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("activation time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_stamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid instruction manifest activation time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("instruction manifest activation time must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class InstructionSource:
    source_kind: str
    content_sha256: str
    version: str = ""
    commit: str = ""
    privacy_class: str = "private_local"

    def __post_init__(self) -> None:
        if not self.source_kind or any(character in self.source_kind for character in "\\/"):
            raise ValueError("instruction source kind must be a generic identifier")
        if not _is_sha256(self.content_sha256):
            raise ValueError("instruction content hash must be lowercase SHA-256")
        if self.commit and (len(self.commit) not in {40, 64}
                            or any(character not in "0123456789abcdef"
                                   for character in self.commit)):
            raise ValueError("instruction source commit must be a full hexadecimal id")
        if not self.version and not self.commit:
            raise ValueError("instruction source requires a version or commit")
        if not self.privacy_class:
            raise ValueError("instruction source requires a privacy class")

    def to_dict(self) -> dict[str, str]:
        payload = {
            "source_kind": self.source_kind,
            "content_sha256": self.content_sha256,
            "privacy_class": self.privacy_class,
        }
        if self.version:
            payload["version"] = self.version
        if self.commit:
            payload["commit"] = self.commit
        return payload


@dataclass(frozen=True)
class InstructionManifest:
    activated_at: datetime
    sources: tuple[InstructionSource, ...]
    manifest_sha256: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported instruction manifest schema")
        if not self.sources:
            raise ValueError("instruction manifest requires at least one source")
        kinds = [source.source_kind for source in self.sources]
        if len(kinds) != len(set(kinds)):
            raise ValueError("instruction manifest source kinds must be unique")
        _stamp(self.activated_at)
        if not _is_sha256(self.manifest_sha256):
            raise ValueError("instruction manifest id must be lowercase SHA-256")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "activated_at": _stamp(self.activated_at),
            "sources": [source.to_dict()
                        for source in sorted(self.sources,
                                             key=lambda item: item.source_kind)],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.canonical_payload(),
                "manifest_sha256": self.manifest_sha256}


def _manifest_digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_instruction_source(path: Path) -> str:
    """Hash one file or a directory tree without exposing its path."""
    path = Path(path)
    digest = hashlib.sha256()
    try:
        if path.is_file() and not path.is_symlink():
            digest.update(b"file\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        if not path.is_dir() or path.is_symlink():
            raise ValueError("instruction source must be a regular file or directory")
        digest.update(b"directory\0")
        files = sorted(item for item in path.rglob("*") if item.is_file())
        for item in files:
            if item.is_symlink():
                raise ValueError("instruction source trees cannot contain symlinks")
            relative = item.relative_to(path).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            with item.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise ValueError("instruction source is unreadable") from exc


def write_instruction_manifest(path: Path, sources, *,
                               activated_at: datetime) -> InstructionManifest:
    path = Path(path)
    _external(path)
    ordered = tuple(sorted(tuple(sources), key=lambda item: item.source_kind))
    provisional = InstructionManifest(
        activated_at=activated_at, sources=ordered,
        manifest_sha256="0" * 64)
    manifest = InstructionManifest(
        activated_at=activated_at, sources=ordered,
        manifest_sha256=_manifest_digest(provisional.canonical_payload()))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".instruction-manifest-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return manifest


def load_instruction_manifest(path: Path) -> InstructionManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        sources = tuple(InstructionSource(
            source_kind=str(item["source_kind"]),
            content_sha256=str(item["content_sha256"]),
            version=str(item.get("version") or ""),
            commit=str(item.get("commit") or ""),
            privacy_class=str(item["privacy_class"]),
        ) for item in payload["sources"])
        manifest = InstructionManifest(
            schema_version=int(payload["schema_version"]),
            activated_at=_parse_stamp(payload["activated_at"]),
            sources=sources,
            manifest_sha256=str(payload["manifest_sha256"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid instruction manifest") from exc
    if _manifest_digest(manifest.canonical_payload()) != manifest.manifest_sha256:
        raise ValueError("instruction manifest fingerprint mismatch")
    return manifest


class InstructionManifestIndex:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def manifests(self) -> tuple[InstructionManifest, ...]:
        return tuple(load_instruction_manifest(path)
                     for path in sorted(self.directory.glob("*.json")))

    def active_at(self, timestamp: datetime) -> InstructionManifest:
        moment = _parse_stamp(_stamp(timestamp))
        eligible = [manifest for manifest in self.manifests()
                    if manifest.activated_at <= moment]
        if not eligible:
            raise ValueError("no active instruction manifest")
        latest = max(manifest.activated_at for manifest in eligible)
        matches = [manifest for manifest in eligible
                   if manifest.activated_at == latest]
        if len(matches) != 1:
            raise ValueError("ambiguous activation boundary")
        return matches[0]

    def coverage(self, timestamps) -> dict[str, object]:
        resolved = unresolved = 0
        hashes = set()
        for timestamp in timestamps:
            try:
                manifest = self.active_at(timestamp)
            except ValueError:
                unresolved += 1
            else:
                resolved += 1
                hashes.add(manifest.manifest_sha256)
        return {"population": resolved + unresolved, "resolved": resolved,
                "unresolved": unresolved,
                "manifest_sha256": sorted(hashes)}
