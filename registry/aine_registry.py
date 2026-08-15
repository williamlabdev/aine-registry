#!/usr/bin/env python3
"""Read-only multi-root AINE Portfolio Intelligence Registry.

The registry discovers local metadata only.  It never edits discovered
projects, installs packages, runs generators, commits, deploys, or mutates a
database.  The only write is an explicitly requested snapshot export.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


SCHEMA = "aine.registry.v1"
DEFAULT_IGNORES = {
    ".git", ".claude", ".codex", ".agents", "node_modules", "target",
    "dist", ".next", "build", "out", "bench-runs", ".venv", "__pycache__",
    ".godot", ".pytest_cache", "coverage", ".orvena", ".cache", "bench",
    "bench-runs", "worktrees", "tmp",
}
DEFAULT_EXCLUDED_PROJECTS: set[str] = set()
MANIFEST_NAMES = {
    "package.json", "Cargo.toml", "go.mod", "pyproject.toml",
    "pnpm-workspace.yaml", "docker-compose.yml", "docker-compose.yaml",
    "Makefile", "manifest.yaml", "requirements.txt",
}
INSTRUCTION_NAMES = {"CLAUDE.md", "AGENTS.md"}
ARTIFACT_NAMES = {
    "SYNC_STAMP", "courses.generated.json", "ros_types.yaml", "specs-manifest.json",
}
PROJECT_MANIFEST = Path(".aine/registry.json")
GENERATED_MARKERS = ("generated", "_gen.", ".generated.", "SYNC_STAMP")
SCAN_SKIP_DIRS = {"data", "media", "models", "checkpoints", "logs", "reports", "fixtures", "vendor", "third_party"}
MAX_ARTIFACTS_PER_PROJECT = 500
LOCAL_PATH_RE = re.compile(r"^(?:/|[A-Za-z]:[\\/])")
SOURCE_TRUTH_SEED: list[dict[str, object]] = []


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


def project_id(root: Path, portfolio_root: Path, workspace_root: Path) -> str:
    known: dict[str, str] = {}
    portfolio_key = rel(portfolio_root, root)
    if portfolio_key in known:
        return known[portfolio_key]
    local_key = rel(workspace_root, root)
    return f"{root_id_for(workspace_root)}.{local_key.replace('/', '.') }".rstrip(".")


def discover_roots(workspace_root: Path, excluded: set[str]) -> tuple[list[Path], list[Path]]:
    roots: list[Path] = []
    excluded_paths: list[Path] = []
    for current, dirs, files in os.walk(workspace_root):
        current_path = Path(current)
        for directory in list(dirs):
            candidate = current_path / directory
            if candidate.is_symlink() and (candidate / ".git").exists():
                roots.append(candidate)
        if ".git" not in dirs and ".git" not in files:
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORES]
            continue
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORES]
        if current_path == workspace_root and current_path.name in excluded:
            excluded_paths.append(current_path)
            continue
        if current_path.name in excluded or current_path.relative_to(workspace_root).as_posix() in excluded:
            excluded_paths.append(current_path)
            dirs[:] = []
            continue
        roots.append(current_path)
    return sorted(set(roots), key=lambda p: (len(p.parts), p.as_posix())), sorted(set(excluded_paths))


def git_metadata(root: Path) -> dict[str, Any]:
    dirty_raw = run_git(root, "status", "--short")
    dirty_paths = [line[3:].strip() for line in dirty_raw.splitlines() if len(line) > 3]
    return {
        "remote": normalized_remote(run_git(root, "config", "--get", "remote.origin.url")),
        "branch": run_git(root, "branch", "--show-current") or "UNKNOWN",
        "commit": run_git(root, "rev-parse", "HEAD") or "UNKNOWN",
        "dirty": bool(dirty_raw), "dirty_paths": dirty_paths[:200],
    }


def classify_project(root: Path) -> str:
    if (root / "pnpm-workspace.yaml").exists(): return "product_monorepo"
    if (root / "Cargo.toml").exists(): return "rust_workspace"
    if (root / "go.mod").exists(): return "service"
    if (root / "pyproject.toml").exists(): return "python_project"
    if (root / "package.json").exists(): return "application_or_library"
    if (root / "manifest.yaml").exists(): return "umbrella_or_research"
    return "unknown"


def runtime_metadata(root: Path) -> dict[str, Any]:
    languages: set[str] = set(); frameworks: set[str] = set(); entrypoints: list[str] = []
    if (root / "package.json").exists() or list(root.glob("**/package.json")): languages.add("javascript/typescript")
    if (root / "Cargo.toml").exists(): languages.add("rust")
    if (root / "go.mod").exists(): languages.add("go")
    if (root / "pyproject.toml").exists() or list(root.glob("**/*.py")): languages.add("python")
    package_manager = None
    package = root / "package.json"
    if package.exists():
        try:
            data = json.loads(read_text(package)); package_manager = data.get("packageManager")
            scripts = data.get("scripts", {})
            if isinstance(scripts, dict): entrypoints.extend(sorted(scripts))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "next" in deps: frameworks.add("nextjs")
            if "vite" in deps: frameworks.add("vite")
            if "turbo" in deps: frameworks.add("turborepo")
        except json.JSONDecodeError: pass
    if (root / "docker-compose.yml").exists() or (root / "docker-compose.yaml").exists(): frameworks.add("docker-compose")
    return {"languages": sorted(languages), "frameworks": sorted(frameworks), "package_manager": package_manager, "entrypoints": entrypoints[:80]}


def file_hash(path: Path) -> dict[str, Any]:
    h = hashlib.sha256(); size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024): h.update(chunk); size += len(chunk)
    except OSError: return {"status": "unreadable"}
    return {"status": "present", "hash": f"sha256:{h.hexdigest()}", "bytes": size}


def project_record(root: Path, portfolio_root: Path, workspace_root: Path, repos: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pid = project_id(root, portfolio_root, workspace_root)
    checkout_id = f"checkout.{root_id_for(workspace_root)}.{rel(workspace_root, root).replace('/', '.') }".rstrip(".")
    repo = repos[checkout_id]
    instructions = [{"path": name, "role": "project_agent_instructions"} for name in sorted(INSTRUCTION_NAMES) if (root / name).exists()]
    commands: dict[str, Any] = {}
    package = root / "package.json"
    if package.exists():
        try:
            scripts = json.loads(read_text(package)).get("scripts", {})
            if isinstance(scripts, dict):
                for name in ("build", "test", "lint", "dev", "verify"):
                    if name in scripts: commands[name] = {"command": f"package script:{name}", "evidence": "package.json"}
        except json.JSONDecodeError: pass
    if (root / "Makefile").exists():
        text = read_text(root / "Makefile")
        for name in ("build", "test", "test-e2e", "lint", "verify"):
            if re.search(rf"^\s*{re.escape(name)}\s*:", text, re.MULTILINE): commands[name] = {"command": f"make {name}", "evidence": "Makefile"}
    root_id = root_id_for(workspace_root)
    path = rel(workspace_root, root)
    return {
        "project_id": pid, "root_id": root_id, "repository_id": repo["repository_id"], "checkout_id": checkout_id,
        "name": root.name, "path": path, "root": path, "kind": classify_project(root),
        "git": repo["git"], "runtime": runtime_metadata(root), "instructions": instructions,
        "commands": commands, "risk": {"default": "L1", "high_risk_paths": []}, "deployment": [],
        "evidence": [f"{path}/{name}" for name in sorted(MANIFEST_NAMES | INSTRUCTION_NAMES) if (root / name).exists()],
    }


def artifact_records(root: Path, workspace_root: Path, pid: str, root_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORES and d not in SCAN_SKIP_DIRS]
        for filename in files:
            path = Path(current) / filename; path_rel = rel(root, path)
            if filename not in ARTIFACT_NAMES and not any(marker in filename for marker in GENERATED_MARKERS): continue
            try:
                if path.stat().st_size > 2_000_000: continue
            except OSError:
                continue
            role = "generated" if any(marker in filename for marker in GENERATED_MARKERS) else "source"
            if filename in {"SYNC_STAMP", "specs-manifest.json"}: role = "provenance"
            aid = f"artifact.{root_id}.{pid}.{path_rel.replace('/', '.').replace('-', '_')}"
            records.append({
                "artifact_id": aid, "project_id": pid, "root_id": root_id, "path": path_rel,
                "workspace_path": f"{rel(workspace_root, root)}/{path_rel}", "artifact_type": path.suffix.lstrip(".") or "file",
                "kind": path.suffix.lstrip(".") or "file", "role": role, "status": "present", "content": file_hash(path),
                "generated": role in {"generated", "provenance"}, "source_of_truth": None,
                "provenance": {"status": "UNKNOWN"}, "consumers": [], "evidence": [f"{rel(workspace_root, root)}/{path_rel}"],
            })
            if len(records) >= MAX_ARTIFACTS_PER_PROJECT: return sorted(records, key=lambda item: item["artifact_id"])
    return sorted(records, key=lambda item: item["artifact_id"])


def portable_manifest_path(value: str) -> str:
    """Keep explicit manifest paths relative and portable."""
    value = str(value).strip()
    if LOCAL_PATH_RE.match(value):
        return "<local-path>"
    return value.removeprefix("./")


def project_manifest(project_root: Path) -> tuple[dict[str, Any], Path | None]:
    path = project_root / PROJECT_MANIFEST
    try:
        data = json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return {}, None
    return (data, path) if isinstance(data, dict) else ({}, None)


def explicit_manifest_records(project: dict[str, Any], project_root: Path, workspace_root: Path, projects: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Load project-owned explicit metadata without executing project code."""
    data, manifest_path = project_manifest(project_root)
    if not data or manifest_path is None:
        return [], [], []
    evidence = f"{rel(workspace_root, project_root)}/{PROJECT_MANIFEST.as_posix()}"
    artifacts: list[dict[str, Any]] = []
    for item in data.get("artifacts", []):
        if not isinstance(item, dict) or not item.get("path"):
            continue
        path = portable_manifest_path(item["path"])
        artifact_id = str(item.get("id") or stable_id("artifact", f"{project['project_id']}:{path}"))
        artifacts.append({
            "artifact_id": artifact_id,
            "project_id": project["project_id"],
            "root_id": project["root_id"],
            "path": path,
            "workspace_path": f"{project['path']}/{path}" if project["path"] != "." else path,
            "artifact_type": Path(path).suffix.lstrip(".") or "file",
            "kind": item.get("kind", Path(path).suffix.lstrip(".") or "file"),
            "role": item.get("role", "unknown"),
            "status": item.get("status", "unknown"),
            "content": {"status": "not_scanned"},
            "generated": item.get("role") in {"generated", "projection", "provenance"},
            "source_of_truth": item.get("source_of_truth"),
            "provenance": item.get("provenance", {"status": "declared", "evidence": [evidence]}),
            "consumers": item.get("consumers", []),
            "evidence": [evidence],
        })
    dependencies: list[dict[str, Any]] = []
    for item in data.get("dependencies", []):
        if not isinstance(item, dict) or not item.get("target"):
            continue
        target_value = str(item["target"])
        target_project = next((p for p in projects if target_value in {p["project_id"], p["name"], p["path"]}), None)
        target_id = target_project["project_id"] if target_project else (target_value if target_value.startswith("external:") else f"external:{target_value}")
        dependencies.append(make_edge(
            project["project_id"], target_id, str(item.get("kind", "declared")), str(item.get("strength", "required")), "declared",
            evidence, {"manifest": evidence, "target": target_value}, projects, target_project,
        ))
    source_rules: list[dict[str, Any]] = []
    for item in data.get("source_of_truth", []):
        if not isinstance(item, dict):
            continue
        rule = json.loads(json.dumps(item))
        rule.setdefault("source_rule_id", stable_id("sot", json.dumps([project["project_id"], rule], sort_keys=True)))
        rule.setdefault("authority", {"project_id": project["project_id"]})
        rule.setdefault("status", "declared")
        rule["evidence"] = sorted(set(rule.get("evidence", []) + [evidence]))
        source_rules.append(rule)
    return artifacts, dependencies, source_rules


def all_package_manifests(root: Path) -> list[Path]:
    return [p for p in [root / "package.json", *root.glob("apps/*/package.json"), *root.glob("packages/*/package.json")] if p.exists()]


def package_edges(root: Path, portfolio_root: Path, workspace_root: Path, pid: str, projects: list[dict[str, Any]], package_index: dict[str, str]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for package_path in all_package_manifests(root):
        try: data = json.loads(read_text(package_path))
        except json.JSONDecodeError: continue
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {}), **data.get("peerDependencies", {})}
        for name, version in sorted(deps.items()):
            if not isinstance(version, str) or not (version.startswith("workspace:")): continue
            provider = package_index.get(name, f"external:{name}")
            provider_project = next((p for p in projects if p["project_id"] == provider), None)
            edge = make_edge(pid, provider, "package", "required", "active", rel(workspace_root, package_path), {"package": name, "version": version}, projects, provider_project)
            edges.append(edge)
    return edges


def project_for_path(path: Path, roots: list[Path], projects_by_root: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    path = path.resolve()
    candidates = [(candidate, projects_by_root[str(candidate)]) for candidate in roots if path == candidate.resolve() or candidate.resolve() in path.parents]
    return max(candidates, key=lambda item: len(item[0].parts))[1] if candidates else None


def make_edge(source_pid: str, target_pid: str, kind: str, strength: str, status: str, evidence: str, reference: dict[str, Any], projects: list[dict[str, Any]], target_project: dict[str, Any] | None = None) -> dict[str, Any]:
    source_project = next((p for p in projects if p["project_id"] == source_pid), None)
    source_root = source_project["root_id"] if source_project else "UNKNOWN"
    target_root = target_project["root_id"] if target_project else (next((p["root_id"] for p in projects if p["project_id"] == target_pid), None) or "external")
    scope = "external" if target_pid.startswith("external:") else ("intra_root" if source_root == target_root else "cross_root")
    source_ref = {"project_id": source_pid, "root_id": source_root}
    target_ref = {"project_id": target_pid, "root_id": target_root}
    edge = {
        "dependency_id": stable_id("dep", json.dumps([source_pid, target_pid, kind, evidence, reference], sort_keys=True)),
        "source": source_ref, "target": target_ref, "consumer": source_ref, "provider": target_ref,
        "dependency_type": kind, "kind": kind, "strength": strength, "scope": scope, "status": status,
        "confidence": "high" if scope != "unknown" else "unknown", "reference": reference,
        "evidence": [evidence], "direct": True,
    }
    return edge


def text_edges(root: Path, workspace_root: Path, pid: str, roots: list[Path], projects_by_root: dict[str, dict[str, Any]], projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patterns = [
        (re.compile(r"(?:/[A-Za-z0-9_./~:@%+-]+|\.\./[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?)"), "filesystem"),
        (re.compile(r"(?:MCP_BASE_URL|MCP_SERVER_URL|API_BASE_URL|SERVICE_URL)"), "runtime_api"),
    ]
    seen: set[str] = set(); edges: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORES and d not in SCAN_SKIP_DIRS]
        for filename in files:
            path = Path(current) / filename
            if path.suffix.lower() not in {".ts", ".tsx", ".js", ".mjs", ".py", ".rs", ".go", ".yaml", ".yml", ".json", ".md", ".toml", ".sh", ".plist"}: continue
            try:
                if path.stat().st_size > 300_000: continue
            except OSError:
                continue
            text = read_text(path, 250_000)
            if not any(hint in text for hint in ("../", *[str(item) for item in roots])) and not re.search(r"(?:^|[\s`'\"])/(?:Users|home|private|var|tmp)/", text):
                continue
            for pattern, kind in patterns:
                for match in pattern.finditer(text):
                    token = match.group(0).rstrip(".,:;)")
                    target_project = None
                    candidate = None
                    if token.startswith("/"):
                        candidate = Path(token).resolve()
                    elif token.startswith("../"):
                        candidate = (path.parent / token).resolve()
                    if candidate:
                        target_project = project_for_path(candidate, roots, projects_by_root)
                        if not target_project and token.startswith("/"):
                            continue
                    target_pid = target_project["project_id"] if target_project else "external:UNKNOWN"
                    if target_pid == pid:
                        continue
                    edge = make_edge(pid, target_pid, kind, "required" if kind in {"build", "runtime_api", "filesystem"} else "optional", "active", f"{rel(workspace_root, path)}", {"reference": token}, projects, target_project)
                    if edge["dependency_id"] not in seen: seen.add(edge["dependency_id"]); edges.append(edge)
    return sorted(edges, key=lambda item: item["dependency_id"])


def source_truth_rules(projects: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return explicitly configured source-of-truth rules.

    The public core does not ship portfolio-specific rules. Projects can add
    rules through a future adapter or an explicit registry manifest.
    """
    return []


def findings(projects: list[dict[str, Any]], artifacts: list[dict[str, Any]], dependencies: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    project_ids = {item["project_id"] for item in projects}
    for edge in dependencies:
        if edge["target"]["project_id"].startswith("external:") or edge["target"]["project_id"] not in project_ids:
            result.append({"finding_id": "DEP-001", "severity": "info", "category": "dependency", "status": "unknown", "subject": edge["dependency_id"], "message": f"Dependency provider is external or unresolved: {edge['target']['project_id']}", "evidence": edge["evidence"]})
    if excluded:
        result.append({"finding_id": "SCOPE-001", "severity": "info", "category": "scope", "status": "declared", "subject": "portfolio.exclusions", "message": "Projects explicitly excluded from the active registry scope are preserved as exclusions.", "evidence": [item["path"] for item in excluded]})
    return result


def canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonicalize(value[key]) for key in sorted(value) if key not in {"snapshot_id", "observed_at", "local_path"}}
    if isinstance(value, list):
        normalized = [canonicalize(item) for item in value]
        if all(isinstance(item, dict) for item in normalized): return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        return normalized
    return value


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(canonicalize(portable_snapshot(snapshot)), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def discover(workspace_roots: list[Path], excluded_names: set[str] | None = None) -> dict[str, Any]:
    excluded_names = set(excluded_names or DEFAULT_EXCLUDED_PROJECTS)
    roots = [path.resolve() for path in workspace_roots]
    portfolio_parent = Path(os.path.commonpath([str(path) for path in roots]))
    root_records = [{"root_id": root_id_for(root), "name": root.name, "path": rel(portfolio_parent, root) or ".", "local_path": str(root), "trust_boundary": "local_workspace", "deployment_boundary": "UNKNOWN", "git_boundary": "independent_git_roots"} for root in roots]
    all_projects: list[Path] = []; excluded_paths: list[Path] = []
    root_for_project: dict[str, Path] = {}
    for workspace_root in roots:
        found, excluded = discover_roots(workspace_root, excluded_names)
        all_projects.extend(found); excluded_paths.extend(excluded)
        for item in found: root_for_project[str(item)] = workspace_root
    all_projects = sorted(set(all_projects), key=lambda p: (len(p.parts), p.as_posix()))
    repo_records: dict[str, dict[str, Any]] = {}; projects_by_root: dict[str, dict[str, Any]] = {}
    for item in all_projects:
        workspace_root = root_for_project[str(item)]; rid = root_id_for(workspace_root); path = rel(workspace_root, item)
        checkout_id = f"checkout.{rid}.{path.replace('/', '.') }".rstrip(".")
        gm = git_metadata(item); remote = gm["remote"]
        repository_id = f"repo.{hashlib.sha1(remote.encode()).hexdigest()[:12]}" if remote != "UNKNOWN" else f"repo.{rid}.{path.replace('/', '.') }".rstrip(".")
        repo_records[checkout_id] = {"repository_id": repository_id, "git": gm}
    for item in all_projects:
        workspace_root = root_for_project[str(item)]
        record = project_record(item, portfolio_parent, workspace_root, repo_records)
        projects_by_root[str(item)] = record
    projects = sorted(projects_by_root.values(), key=lambda p: p["project_id"])
    active_ids = {p["project_id"] for p in projects}
    repos = {}
    checkouts = []
    for item in all_projects:
        p = projects_by_root[str(item)]; checkout_id = p["checkout_id"]
        if p["name"] in excluded_names:
            excluded_paths.append(item); continue
        repos[p["repository_id"]] = {"repository_id": p["repository_id"], "canonical_identity": p["git"]["remote"], "git": {"remote": p["git"]["remote"]}}
        checkouts.append({"checkout_id": checkout_id, "repository_id": p["repository_id"], "root_id": p["root_id"], "path": p["path"], "branch": p["git"]["branch"], "commit": p["git"]["commit"], "dirty": p["git"]["dirty"], "dirty_paths": p["git"]["dirty_paths"]})
    projects = [p for p in projects if p["project_id"] in active_ids and p["name"] not in excluded_names]
    active_root_paths = [item for item in all_projects if projects_by_root[str(item)]["name"] not in excluded_names]
    projects_by_root = {str(path): projects_by_root[str(path)] for path in active_root_paths}
    artifacts: list[dict[str, Any]] = []
    manifest_records: list[tuple[dict[str, Any], Path, Path]] = []
    for item in active_root_paths:
        p = projects_by_root[str(item)]; artifacts.extend(artifact_records(item, root_for_project[str(item)], p["project_id"], p["root_id"]))
        manifest_records.append((p, item, root_for_project[str(item)]))
    manifest_artifacts: list[dict[str, Any]] = []
    manifest_dependencies: list[dict[str, Any]] = []
    manifest_source_truth: list[dict[str, Any]] = []
    for project, project_root, workspace_root in manifest_records:
        explicit_artifacts, explicit_dependencies, explicit_source_truth = explicit_manifest_records(project, project_root, workspace_root, projects)
        manifest_artifacts.extend(explicit_artifacts)
        manifest_dependencies.extend(explicit_dependencies)
        manifest_source_truth.extend(explicit_source_truth)
    artifacts.extend(manifest_artifacts)
    package_index: dict[str, str] = {}
    for item in active_root_paths:
        p = projects_by_root[str(item)]
        for manifest in all_package_manifests(item):
            try:
                name = json.loads(read_text(manifest)).get("name")
                if name: package_index[name] = p["project_id"]
            except json.JSONDecodeError: pass
    dependencies: list[dict[str, Any]] = []
    for item in active_root_paths:
        p = projects_by_root[str(item)]; workspace_root = root_for_project[str(item)]
        dependencies.extend(package_edges(item, portfolio_parent, workspace_root, p["project_id"], projects, package_index))
        dependencies.extend(text_edges(item, workspace_root, p["project_id"], active_root_paths, projects_by_root, projects))
    dependencies.extend(manifest_dependencies)
    grouped: dict[str, dict[str, Any]] = {}
    for edge in dependencies:
        grouping_key = json.dumps([edge["source"], edge["target"], edge["kind"], edge.get("reference", {})], ensure_ascii=False, sort_keys=True)
        if grouping_key not in grouped:
            grouped[grouping_key] = dict(edge)
            grouped[grouping_key]["dependency_id"] = stable_id("dep", grouping_key)
        else:
            grouped[grouping_key]["evidence"] = sorted(set(grouped[grouping_key]["evidence"] + edge["evidence"]))[:100]
    dependencies = sorted(grouped.values(), key=lambda e: e["dependency_id"])
    excluded = [{"name": Path(path).name, "path": str(path), "reason": "excluded_from_active_registry"} for path in sorted(set(excluded_paths))]
    root_data = [{k: v for k, v in item.items() if k != "local_path"} for item in root_records]
    snapshot: dict[str, Any] = {
        "schema": SCHEMA, "snapshot_id": None,
        "portfolio": {"portfolio_id": "default.portfolio", "name": "Default Portfolio", "workspace_roots": root_data},
        "workspace": {"roots": root_data, "root": root_data[0]["path"] if len(root_data) == 1 else None, "observed_at": datetime.now(timezone.utc).isoformat()},
        "repositories": sorted(repos.values(), key=lambda r: r["repository_id"]), "checkouts": sorted(checkouts, key=lambda c: c["checkout_id"]),
        "projects": projects, "artifacts": sorted(artifacts, key=lambda a: a["artifact_id"]), "dependencies": dependencies,
        "source_of_truth": source_truth_rules(projects, artifacts) + manifest_source_truth, "findings": [], "exclusions": sorted(DEFAULT_IGNORES), "excluded_projects": excluded,
        "_local_roots": [{"root_id": item["root_id"], "local_path": item["local_path"]} for item in root_records],
    }
    snapshot["findings"] = findings(projects, artifacts, dependencies, excluded)
    snapshot["snapshot_id"] = snapshot_hash(snapshot)
    return portable_snapshot(snapshot)


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


def no_absolute_paths(value: Any) -> bool:
    if isinstance(value, dict): return all(no_absolute_paths(item) for item in value.values())
    if isinstance(value, list): return all(no_absolute_paths(item) for item in value)
    return not (isinstance(value, str) and LOCAL_PATH_RE.match(value))


def write_local_config(args: argparse.Namespace) -> int:
    entries = getattr(args, "init_roots", None) or []
    roots = []
    for entry in entries:
        if "=" not in entry:
            print("--root must use root_id=/local/path", file=sys.stderr); return 2
        root_id, path = entry.split("=", 1)
        roots.append({"id": root_id, "path": str(Path(path).expanduser().resolve())})
    if not roots:
        print("init requires at least one --root root_id=/local/path", file=sys.stderr); return 2
    path = config_path(args); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"portfolio": {"name": "default"}, "workspace_roots": roots}, indent=2) + "\n", encoding="utf-8")
    print_json({"status": "initialized", "config": ".aine/portfolio.local.json", "workspace_roots": [{"id": item["id"]} for item in roots]})
    return 0


def project_matches(snapshot: dict[str, Any], value: str) -> list[dict[str, Any]]:
    return [p for p in snapshot["projects"] if value in {p["project_id"], p["name"], p["path"], p["checkout_id"], p["repository_id"]} or value in p["project_id"]]


def impact(snapshot: dict[str, Any], seed: str) -> dict[str, Any]:
    matched = project_matches(snapshot, seed)
    matched_artifacts = [a for a in snapshot["artifacts"] if seed in a["artifact_id"] or seed in a.get("workspace_path", "") or seed == a.get("path")]
    seed_ids = {p["project_id"] for p in matched} | {a["project_id"] for a in matched_artifacts}
    direct: list[dict[str, Any]] = []; transitive: list[dict[str, Any]] = []; visited = set(seed_ids); frontier = set(seed_ids); depth = 0
    while frontier:
        next_frontier: set[str] = set()
        for edge in snapshot["dependencies"]:
            source = edge["source"]["project_id"]; target = edge["target"]["project_id"]
            if target not in frontier: continue
            item = dict(edge); item["impact_depth"] = depth + 1; item["direct"] = depth == 0
            (direct if depth == 0 else transitive).append(item)
            if source not in visited and not source.startswith("external:"):
                visited.add(source); next_frontier.add(source)
        frontier = next_frontier; depth += 1
    return {"query": seed, "matched_projects": matched, "matched_artifacts": matched_artifacts, "direct_edges": direct, "transitive_edges": transitive, "cross_root": any(e["scope"] == "cross_root" for e in direct + transitive), "source_of_truth": [r for r in snapshot["source_of_truth"] if seed in json.dumps(r, ensure_ascii=False)], "findings": snapshot["findings"]}


def change_matches(snapshot: dict[str, Any], change: str, roots: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve a changed path to registry projects and artifacts."""
    candidates = {change, change.removeprefix("./")}
    change_path = Path(change).expanduser()
    if not change_path.is_absolute():
        relative = change_path.as_posix().removeprefix("./")
        for root in roots:
            candidate_path = root / relative
            if candidate_path.exists():
                root_id = root_id_for(root)
                candidates.add(relative)
                for project in snapshot["projects"]:
                    if project["root_id"] == root_id and project["path"] in {"", "."}:
                        candidates.add(project["project_id"])
    if change_path.is_absolute():
        for root in roots:
            try:
                relative = change_path.resolve().relative_to(root.resolve()).as_posix()
            except (OSError, ValueError):
                continue
            root_id = root_id_for(root)
            candidates.update({f"{root_id}:{relative}", relative})
            for project in snapshot["projects"]:
                if project["root_id"] != root_id:
                    continue
                project_path = project["path"].rstrip("/")
                if project_path in {"", "."} or relative == project_path or relative.startswith(project_path + "/"):
                    candidates.add(project["project_id"])
    projects = [p for p in snapshot["projects"] if any(value in candidates for value in {p["project_id"], p["name"], p["path"], p.get("workspace_path", "")})]
    artifacts = [a for a in snapshot["artifacts"] if any(value in candidates for value in {a["artifact_id"], a["path"], a.get("workspace_path", "")})]
    artifact_project_ids = {a["project_id"] for a in artifacts}
    projects.extend(p for p in snapshot["projects"] if p["project_id"] in artifact_project_ids and p not in projects)
    return projects, artifacts


def preflight(snapshot: dict[str, Any], changes: list[str], roots: list[Path]) -> dict[str, Any]:
    matched_projects: list[dict[str, Any]] = []
    matched_artifacts: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for change in changes:
        projects, artifacts = change_matches(snapshot, change, roots)
        if not projects and not artifacts:
            unresolved.append(change)
        for project in projects:
            if project not in matched_projects:
                matched_projects.append(project)
        for artifact in artifacts:
            if artifact not in matched_artifacts:
                matched_artifacts.append(artifact)
    impact_reports = [impact(snapshot, p["project_id"]) for p in matched_projects]
    affected_ids = {p["project_id"] for p in matched_projects}
    for report in impact_reports:
        for edge in report["direct_edges"] + report["transitive_edges"]:
            affected_ids.add(edge["source"]["project_id"])
    affected_projects = [p for p in snapshot["projects"] if p["project_id"] in affected_ids]
    validation: list[dict[str, Any]] = []
    for project in affected_projects:
        for name in ("test", "verify", "lint", "build"):
            command_info = project.get("commands", {}).get(name)
            if command_info:
                validation.append({"project_id": project["project_id"], "check": name, **command_info})
    related_rules = []
    for rule in snapshot["source_of_truth"]:
        serialized = json.dumps(rule, ensure_ascii=False)
        if any(project["project_id"] in serialized for project in affected_projects) or any(artifact["artifact_id"] in serialized for artifact in matched_artifacts):
            related_rules.append(rule)
    unknowns = list(snapshot["findings"])
    if unresolved:
        unknowns.append({"finding_id": "PREFLIGHT-001", "severity": "medium", "category": "change", "status": "unknown", "subject": "unresolved_changes", "message": "One or more changes did not match a registered project or artifact.", "evidence": unresolved})
    if not matched_projects and not matched_artifacts:
        unknowns.append({"finding_id": "PREFLIGHT-002", "severity": "high", "category": "boundary", "status": "human_review_required", "subject": "change_scope", "message": "The change is outside the known registry boundary; do not assume it is safe to modify.", "evidence": changes})
    return {
        "changes": changes,
        "matched_projects": matched_projects,
        "matched_artifacts": matched_artifacts,
        "affected_projects": affected_projects,
        "impact": impact_reports,
        "source_of_truth": related_rules,
        "required_validation": validation,
        "unknowns": unknowns,
        "read_only": True,
        "snapshot_id": snapshot["snapshot_id"],
    }


def command(args: argparse.Namespace) -> int:
    if args.action == "init":
        return write_local_config(args)
    if args.action == "portfolio" and args.portfolio_action == "list":
        snapshot = load_snapshot(args); print_json(snapshot["portfolio"]); return 0
    roots = configured_roots(args)
    if any(not root.is_dir() for root in roots): print("workspace root does not exist", file=sys.stderr); return 2
    snapshot = load_snapshot(args) if args.snapshot else discover(roots, set(args.exclude_project or DEFAULT_EXCLUDED_PROJECTS))
    action = args.action
    if action == "portfolio": action = args.portfolio_action
    if action in {"discover", "scan", "portfolio-discover"}:
        if args.output:
            output = Path(args.output).resolve(); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else: print_json(snapshot)
    elif action in {"projects", "project"}: print_json(snapshot["projects"])
    elif action in {"repositories", "repo"}: print_json(snapshot["repositories"])
    elif action in {"checkouts", "checkout"}: print_json(snapshot["checkouts"])
    elif action in {"artifacts", "artifact"}: print_json(snapshot["artifacts"])
    elif action in {"dependencies", "deps", "dependency"}: print_json(snapshot["dependencies"])
    elif action in {"dependency-graph", "graph"}: print_json({"projects": snapshot["projects"], "dependencies": snapshot["dependencies"]})
    elif action in {"findings"}: print_json(snapshot["findings"])
    elif action == "source-of-truth": print_json([r for r in snapshot["source_of_truth"] if args.domain in r["domain"]])
    elif action == "impact":
        seed = args.project or args.path or args.artifact
        if not seed: print("impact requires --project, --path, or --artifact", file=sys.stderr); return 2
        print_json(impact(snapshot, seed))
    elif action == "preflight":
        changes = args.change or []
        if not changes: print("preflight requires at least one --change", file=sys.stderr); return 2
        print_json(preflight(snapshot, changes, roots))
    elif action == "workspace": print_json(snapshot["portfolio"]["workspace_roots"])
    elif action == "context": print_json({"portfolio": snapshot["portfolio"], "projects": snapshot["projects"], "snapshot_id": snapshot["snapshot_id"]})
    elif action == "validate": print_json({"valid": no_absolute_paths(snapshot), "snapshot_id": snapshot["snapshot_id"], "findings": snapshot["findings"]})
    elif action == "handoff": print_json({"portfolio_id": snapshot["portfolio"]["portfolio_id"], "snapshot_id": snapshot["snapshot_id"], "projects": [{"project_id": p["project_id"], "root_id": p["root_id"], "checkout_id": p["checkout_id"], "path": p["path"]} for p in snapshot["projects"]], "cross_root_dependencies": sum(e["scope"] == "cross_root" for e in snapshot["dependencies"])})
    else: return 2
    return 0


def load_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    if not args.snapshot: return discover(configured_roots(args))
    try: return portable_snapshot(json.loads(Path(args.snapshot).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc: raise SystemExit(f"could not read snapshot: {exc}")


def add_workspace_options(command_parser: argparse.ArgumentParser) -> None:
    """Allow the ergonomic `aine command --root ...` form.

    Global options remain supported for backwards compatibility. Suppressed
    defaults prevent subcommand options from overwriting global values.
    """
    command_parser.add_argument("--workspace", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    command_parser.add_argument("--root", dest="roots", action="append", default=argparse.SUPPRESS, help="workspace root; repeat for multi-root discovery")
    command_parser.add_argument("--snapshot", default=argparse.SUPPRESS, help="read an existing JSON snapshot")
    command_parser.add_argument("--exclude-project", action="append", default=argparse.SUPPRESS, help="exclude project name; repeat as needed")
    command_parser.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Read-only AINE multi-root portfolio registry")
    p.add_argument("--workspace", help="legacy single workspace root")
    p.add_argument("--root", dest="roots", action="append", help="workspace root; repeat for multi-root discovery")
    p.add_argument("--snapshot", help="read an existing JSON snapshot")
    p.add_argument("--exclude-project", action="append", help="exclude project name; repeat as needed")
    p.add_argument("--config", help="local-only config path; never included in portable snapshots")
    sub = p.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init"); init.add_argument("--root", dest="init_roots", action="append", required=True)
    discover_cmd = sub.add_parser("discover"); discover_cmd.add_argument("positional_roots", nargs="*"); discover_cmd.add_argument("--output"); add_workspace_options(discover_cmd)
    scan_cmd = sub.add_parser("scan", help="discover projects and artifacts (alias for discover)"); scan_cmd.add_argument("positional_roots", nargs="*"); scan_cmd.add_argument("--output"); add_workspace_options(scan_cmd)
    for name in ("projects", "project", "repositories", "repo", "checkouts", "checkout", "artifacts", "artifact", "dependencies", "deps", "dependency-graph", "graph", "findings", "workspace", "context", "validate", "handoff"):
        child = sub.add_parser(name)
        if name in {"context", "validate", "handoff", "workspace", "findings", "projects", "project", "repositories", "repo", "checkouts", "checkout", "artifacts", "artifact", "dependencies", "deps", "dependency-graph", "graph"}:
            add_workspace_options(child)
        if name in {"project", "repo", "checkout", "artifact", "dependency", "workspace"}:
            child.add_argument("subcommand", nargs="?")
    dependency = sub.add_parser("dependency"); dependency.add_argument("subcommand", nargs="?")
    sot = sub.add_parser("source-of-truth"); sot.add_argument("domain")
    impact_cmd = sub.add_parser("impact"); impact_cmd.add_argument("target", nargs="?"); impact_cmd.add_argument("--project"); impact_cmd.add_argument("--path"); impact_cmd.add_argument("--artifact"); add_workspace_options(impact_cmd)
    preflight_cmd = sub.add_parser("preflight", help="analyze a proposed change without mutating the workspace"); preflight_cmd.add_argument("--change", action="append", required=True, help="changed path, artifact, or project; repeat as needed"); add_workspace_options(preflight_cmd)
    portfolio = sub.add_parser("portfolio"); portfolio_sub = portfolio.add_subparsers(dest="portfolio_action", required=True); pd = portfolio_sub.add_parser("discover"); pd.add_argument("--root", dest="portfolio_roots", action="append"); pd.add_argument("--output"); portfolio_sub.add_parser("list")
    return p


def main(argv: list[str] | None = None) -> int:
    parsed = parser().parse_args(argv)
    if parsed.action in {"discover", "scan"} and getattr(parsed, "positional_roots", None):
        parsed.roots = parsed.positional_roots
    if parsed.action == "impact" and getattr(parsed, "target", None) and not (parsed.project or parsed.path or parsed.artifact): parsed.project = parsed.target
    return command(parsed)


if __name__ == "__main__":
    raise SystemExit(main())
