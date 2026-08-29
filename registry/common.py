"""Portable identity, filesystem, and local configuration helpers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from .constants import INVENTORY_KEY, LOCAL_PATH_RE, PUBLISHED_PROJECTS_KEY, RELATIONSHIP_OVERLAY_KEY
    from . import inventory
except ImportError:  # direct execution compatibility
    from constants import INVENTORY_KEY, LOCAL_PATH_RE, PUBLISHED_PROJECTS_KEY, RELATIONSHIP_OVERLAY_KEY
    import inventory


def run_git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def read_text(path: Path, limit: int = 1_000_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}.{hashlib.sha1(value.encode()).hexdigest()[:12]}"


def normalized_remote(remote: str) -> str:
    remote = remote.strip()
    if remote.startswith("git@") and ":" in remote:
        remote = "https://" + remote.split(":", 1)[1]
    remote = re.sub(r"\.git$", "", remote).rstrip("/")
    if "://" in remote:
        parsed = urlsplit(remote)
        if parsed.hostname:
            host = parsed.hostname
            if parsed.port:
                host = f"{host}:{parsed.port}"
            remote = urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment)).rstrip("/")
    return remote or "UNKNOWN"


def portableize(value: Any, roots: list[dict[str, Any]]) -> Any:
    """Remove machine-local path identity from portable registry material."""
    if isinstance(value, dict):
        return {key: portableize(item, roots) for key, item in value.items() if key not in {"local_path"}}
    if isinstance(value, list):
        return [portableize(item, roots) for item in value]
    if not isinstance(value, str) or not LOCAL_PATH_RE.match(value):
        return value
    for root in sorted(roots, key=lambda item: len(item.get("local_path", "")), reverse=True):
        local = root.get("local_path")
        if local:
            try:
                local_path = Path(local).resolve()
                value_path = Path(value).resolve()
                if value_path == local_path or local_path in value_path.parents:
                    suffix = value_path.relative_to(local_path).as_posix()
                    return f"{root['root_id']}:{suffix}" if suffix != "." else root["root_id"]
            except (OSError, ValueError):
                if value == local or value.startswith(local.rstrip("/") + "/"):
                    suffix = value[len(local):].lstrip("/")
                    return f"{root['root_id']}:{suffix}" if suffix else root["root_id"]
    return "<local-path>"


def portable_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    roots = snapshot.get("_local_roots", [])
    clean = portableize(snapshot, roots)
    clean.pop("_local_roots", None)
    return clean


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def root_id_for(path: Path) -> str:
    name = path.name.lower().replace("_", "-").replace(" ", "-")
    return name or "root"


def root_identifiers(paths: list[Path]) -> dict[str, str]:
    counts: dict[str, int] = {}
    result: dict[str, str] = {}
    for path in paths:
        base = root_id_for(path)
        counts[base] = counts.get(base, 0) + 1
        result[str(path)] = base if counts[base] == 1 else f"{base}-{counts[base]}"
    return result


def project_id(root: Path, portfolio_root: Path, workspace_root: Path, workspace_root_id: str | None = None) -> str:
    known: dict[str, str] = {}
    portfolio_key = rel(portfolio_root, root)
    if portfolio_key in known:
        return known[portfolio_key]
    local_key = rel(workspace_root, root)
    return f"{workspace_root_id or root_id_for(workspace_root)}.{local_key.replace('/', '.') }".rstrip(".")


def file_hash(path: Path) -> dict[str, Any]:
    h = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                h.update(chunk)
                size += len(chunk)
    except OSError:
        return {"status": "unreadable"}
    return {"status": "present", "hash": f"sha256:{h.hexdigest()}", "bytes": size}


def portable_manifest_path(value: str) -> str:
    """Keep explicit manifest paths relative and portable."""
    value = str(value).strip()
    if LOCAL_PATH_RE.match(value):
        return "<local-path>"
    return value.removeprefix("./")


def canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonicalize(value[key]) for key in sorted(value) if key not in {"snapshot_id", "observed_at", "local_path"}}
    if isinstance(value, list):
        normalized = [canonicalize(item) for item in value]
        if all(isinstance(item, dict) for item in normalized):
            return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        return normalized
    return value


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(canonicalize(portable_snapshot(snapshot)), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def no_absolute_paths(value: Any) -> bool:
    if isinstance(value, dict):
        return all(no_absolute_paths(item) for item in value.values())
    if isinstance(value, list):
        return all(no_absolute_paths(item) for item in value)
    return not (isinstance(value, str) and LOCAL_PATH_RE.match(value))


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def config_path(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "config", None) or ".aine/portfolio.local.json").expanduser().resolve()


def load_local_config(args: argparse.Namespace) -> dict[str, Any]:
    path = config_path(args)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def configured_roots(args: argparse.Namespace) -> list[Path]:
    config = load_local_config(args)
    configured = args.roots or getattr(args, "portfolio_roots", None) or []
    if not configured and args.workspace:
        configured = [args.workspace]
    if not configured:
        configured = [item.get("id") for item in config.get("workspace_roots", []) if item.get("id")]
    by_id = {item.get("id"): item.get("path") for item in config.get("workspace_roots", []) if item.get("id") and item.get("path")}
    resolved: list[Path] = []
    for value in configured:
        candidate = by_id.get(value, value)
        resolved.append(Path(candidate).expanduser().resolve())
    return resolved or [Path(".").resolve()]


def configured_overlays(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Relationship declarations kept out of the project repositories.

    A project manifest travels with its project, so a target named there is
    published wherever the project is published. Edges that must not be
    published -- typically into private projects -- are declared in the local
    configuration instead and merged at discovery time. The declaration is
    identical to a manifest ``relationships`` entry; only its home differs.
    """
    overlays = load_local_config(args).get(RELATIONSHIP_OVERLAY_KEY, [])
    if not isinstance(overlays, list):
        return []
    return [
        item for item in overlays
        if isinstance(item, dict) and item.get("project") and isinstance(item.get("relationships", []), list)
    ]


def configured_inventory(args: argparse.Namespace) -> dict[str, Any] | None:
    """The portfolio inventory to join, if one is declared.

    Undeclared, discovery classifies nothing and behaves exactly as before. Once
    declared, a missing or malformed file raises: an operator who asked for the
    join and silently got no classification would read the absence as "nothing
    to classify" rather than as a broken configuration.
    """
    declared = getattr(args, "inventory", None) or load_local_config(args).get(INVENTORY_KEY)
    if not isinstance(declared, str) or not declared.strip():
        return None
    return inventory.load(Path(declared).expanduser().resolve())


def configured_published_projects(args: argparse.Namespace) -> list[str]:
    """Project references whose repositories are published.

    Left undeclared the disclosure check stays inert: an operator who has not said
    which projects are public gets no findings rather than a finding for every edge.
    """
    declared = load_local_config(args).get(PUBLISHED_PROJECTS_KEY, [])
    if not isinstance(declared, list):
        return []
    return [str(item) for item in declared if isinstance(item, str) and item.strip()]
