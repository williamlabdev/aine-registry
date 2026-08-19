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
API_CONTRACT_NAMES = {
    "api.json", "api.yaml", "api.yml", "openapi.json", "openapi.yaml", "openapi.yml",
    "swagger.json", "swagger.yaml", "swagger.yml",
}
ASYNCAPI_CONTRACT_NAMES = {"asyncapi.json", "asyncapi.yaml", "asyncapi.yml", "events.yaml", "events.yml"}
DEPLOYMENT_NAMES = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "chart.yaml"}
PROJECT_MANIFEST = Path(".aine/registry.json")
GENERATED_MARKERS = ("generated", "_gen.", ".generated.", "SYNC_STAMP")
SCAN_SKIP_DIRS = {"data", "media", "models", "checkpoints", "logs", "reports", "fixtures", "vendor", "third_party"}
IMPORT_SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs"}
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


def project_record(root: Path, portfolio_root: Path, workspace_root: Path, repos: dict[str, dict[str, Any]], workspace_root_id: str | None = None) -> dict[str, Any]:
    root_id = workspace_root_id or root_id_for(workspace_root)
    pid = project_id(root, portfolio_root, workspace_root, root_id)
    checkout_id = f"checkout.{root_id}.{rel(workspace_root, root).replace('/', '.') }".rstrip(".")
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
    path = rel(workspace_root, root)
    return {
        "project_id": pid, "root_id": root_id, "repository_id": repo["repository_id"], "checkout_id": checkout_id,
        "name": root.name, "path": path, "root": path, "kind": classify_project(root),
        "git": repo["git"], "runtime": runtime_metadata(root), "instructions": instructions,
        "commands": commands, "owner": "UNKNOWN", "ownership": {"team": "UNKNOWN", "owners": [], "delegates": []}, "capabilities": [], "risk": {"default": "low", "high_risk_paths": []}, "approval_required": False, "policy": {}, "deployment": [],
        "evidence": [f"{path}/{name}" for name in sorted(MANIFEST_NAMES | INSTRUCTION_NAMES) if (root / name).exists()],
    }


def openapi_metadata(path: Path) -> dict[str, str] | None:
    """Detect an OpenAPI contract without requiring a YAML dependency."""
    if path.name.lower() not in API_CONTRACT_NAMES:
        return None
    text = read_text(path, 200_000)
    json_match = re.search(r'"openapi"\s*:\s*["\']?([0-9]+(?:\.[0-9]+)*)', text)
    yaml_match = re.search(r"(?:^|\n)\s*openapi\s*:\s*[\"']?([0-9]+(?:\.[0-9]+)*)", text)
    version = (json_match or yaml_match).group(1) if (json_match or yaml_match) else None
    if not version:
        return None
    return {"format": "openapi", "version": version}


def protobuf_metadata(path: Path) -> dict[str, Any] | None:
    """Detect a protobuf schema and collect only lightweight declarations."""
    if path.suffix.lower() != ".proto":
        return None
    text = read_text(path, 300_000)
    if not re.search(r"\b(?:syntax|package|message|enum|service|rpc)\b", text):
        return None
    syntax_match = re.search(r'\bsyntax\s*=\s*["\'](proto[23])["\']', text)
    package_match = re.search(r"\bpackage\s+([A-Za-z_][\w.]*)\s*;", text)
    services = sorted(set(re.findall(r"\bservice\s+([A-Za-z_][\w]*)\s*\{", text)))
    return {
        "format": "protobuf",
        "syntax": syntax_match.group(1) if syntax_match else "UNKNOWN",
        "package": package_match.group(1) if package_match else "UNKNOWN",
        "services": services,
    }


def asyncapi_metadata(path: Path) -> dict[str, Any] | None:
    """Detect an AsyncAPI document without requiring a JSON/YAML library."""
    if path.name.lower() not in ASYNCAPI_CONTRACT_NAMES:
        return None
    text = read_text(path, 300_000)
    json_match = re.search(r'"asyncapi"\s*:\s*["\']?([0-9]+(?:\.[0-9]+)*)', text)
    yaml_match = re.search(r"(?:^|\n)\s*asyncapi\s*:\s*[\"']?([0-9]+(?:\.[0-9]+)*)", text)
    has_channels = bool(re.search(r"(?:^|\n)\s*channels\s*:", text) or '"channels"' in text)
    if not (json_match or yaml_match) or not has_channels:
        return None
    version = (json_match or yaml_match).group(1)
    channel_count = len(re.findall(r"(?:^|\n)\s{2,8}[^\s#][^:#]*:\s*$", text))
    return {"format": "asyncapi", "version": version, "channel_count": channel_count}


def deployment_metadata(path: Path) -> dict[str, str] | None:
    """Recognize common deployment descriptors without executing their tools."""
    name = path.name.lower()
    if name == "dockerfile" or name.startswith("dockerfile."):
        return {"format": "docker", "kind": "dockerfile"}
    if name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
        text = read_text(path, 200_000)
        if re.search(r"(?:^|\n)\s*(?:services|version)\s*:", text):
            return {"format": "docker-compose", "kind": "compose"}
    if name == "chart.yaml":
        text = read_text(path, 100_000)
        if re.search(r"(?:^|\n)\s*apiVersion\s*:\s*[^\s#]+", text) and re.search(r"(?:^|\n)\s*name\s*:\s*[^\s#]+", text):
            return {"format": "helm", "kind": "helm_chart"}
    if path.suffix.lower() in {".yaml", ".yml"}:
        text = read_text(path, 200_000)
        api_match = re.search(r"(?:^|\n)\s*apiVersion\s*:\s*([^\s#]+)", text)
        kind_match = re.search(r"(?:^|\n)\s*kind\s*:\s*([^\s#]+)", text)
        if api_match and kind_match and kind_match.group(1) in {"Deployment", "StatefulSet", "DaemonSet", "Service", "Job", "CronJob", "Ingress", "ConfigMap", "Secret"}:
            return {"format": "kubernetes", "kind": "kubernetes_manifest"}
    return None


def ci_metadata(path: Path, project_root: Path) -> dict[str, Any] | None:
    """Inventory GitHub Actions workflow structure without executing it."""
    try:
        relative = path.relative_to(project_root)
    except ValueError:
        return None
    if len(relative.parts) != 3 or relative.parts[0:2] != (".github", "workflows") or path.suffix.lower() not in {".yml", ".yaml"}:
        return None
    text = read_text(path, 200_000)
    if not re.search(r"(?:^|\n)\s*jobs\s*:", text):
        return None
    jobs_section = re.split(r"^jobs[ \t]*:[ \t]*$", text, maxsplit=1, flags=re.MULTILINE)[-1]
    jobs_section = re.split(r"^[A-Za-z_][\w-]*[ \t]*:", jobs_section, maxsplit=1, flags=re.MULTILINE)[0]
    jobs = sorted(set(re.findall(r"^[ \t]{2}([A-Za-z_][\w-]*)[ \t]*:[ \t]*$", jobs_section, re.MULTILINE)))
    return {"provider": "github_actions", "kind": "workflow", "jobs": jobs}


def artifact_records(root: Path, workspace_root: Path, pid: str, root_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORES and d not in SCAN_SKIP_DIRS]
        for filename in files:
            path = Path(current) / filename; path_rel = rel(root, path)
            contract = openapi_metadata(path)
            protobuf = protobuf_metadata(path)
            asyncapi = asyncapi_metadata(path)
            deployment = deployment_metadata(path)
            ci = ci_metadata(path, root)
            if filename not in ARTIFACT_NAMES and not any(marker in filename for marker in GENERATED_MARKERS) and contract is None and protobuf is None and asyncapi is None and deployment is None and ci is None: continue
            try:
                if path.stat().st_size > 2_000_000: continue
            except OSError:
                continue
            role = "generated" if any(marker in filename for marker in GENERATED_MARKERS) else "source"
            if filename in {"SYNC_STAMP", "specs-manifest.json"}: role = "provenance"
            if contract or protobuf or asyncapi: role = "schema"
            if deployment: role = "deployment"
            if ci: role = "provenance"
            aid = f"artifact.{root_id}.{pid}.{path_rel.replace('/', '.').replace('-', '_')}"
            records.append({
                "artifact_id": aid, "project_id": pid, "root_id": root_id, "path": path_rel,
                "workspace_path": f"{rel(workspace_root, root)}/{path_rel}", "artifact_type": path.suffix.lstrip(".") or "file",
                "kind": "openapi_contract" if contract else ("protobuf_contract" if protobuf else ("asyncapi_contract" if asyncapi else (deployment["kind"] if deployment else ("github_actions_workflow" if ci else (path.suffix.lstrip(".") or "file"))))), "role": role, "status": "present", "content": file_hash(path),
                "generated": role in {"generated", "provenance"}, "source_of_truth": None,
                "provenance": {"status": "UNKNOWN"}, "consumers": [], "evidence": [f"{rel(workspace_root, root)}/{path_rel}"],
                **({"contract": contract or protobuf or asyncapi} if contract or protobuf or asyncapi else {}),
                **({"deployment": deployment} if deployment else {}),
                **({"ci": ci} if ci else {}),
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
    project_metadata = data.get("project", {})
    if isinstance(project_metadata, dict):
        project["owner"] = project_metadata.get("owner", project.get("owner", "UNKNOWN"))
        ownership = project_metadata.get("ownership", project.get("ownership", {}))
        if isinstance(ownership, dict):
            project["ownership"] = {
                "team": str(ownership.get("team", project.get("ownership", {}).get("team", "UNKNOWN"))),
                "owners": [str(item) for item in ownership.get("owners", project.get("ownership", {}).get("owners", []))],
                "delegates": [str(item) for item in ownership.get("delegates", project.get("ownership", {}).get("delegates", []))],
            }
        project["capabilities"] = project_metadata.get("capabilities", project.get("capabilities", []))
        project["risk"] = {**project.get("risk", {}), **project_metadata.get("risk", {})} if isinstance(project_metadata.get("risk", {}), dict) else project.get("risk", {})
        project["approval_required"] = bool(project_metadata.get("approval_required", project.get("approval_required", False)))
        project["policy"] = project_metadata.get("policy", project.get("policy", {})) if isinstance(project_metadata.get("policy", project.get("policy", {})), dict) else project.get("policy", {})
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
            "risk": str(item.get("risk", "medium" if item.get("role") in {"source", "generated", "projection", "schema", "deployment"} else "low")).lower(),
            "approval_required": bool(item.get("approval_required", False)),
            "provenance": item.get("provenance", {"status": "declared", "evidence": [evidence]}),
            "consumers": item.get("consumers", []),
            "evidence": [evidence],
        })
    dependencies: list[dict[str, Any]] = []
    declared_relationships = [(item, False) for item in data.get("dependencies", [])] + [(item, True) for item in data.get("relationships", [])]
    for item, is_relationship in declared_relationships:
        if not isinstance(item, dict) or not item.get("target"):
            continue
        target_value = str(item["target"])
        target_project = next((p for p in projects if target_value in {p["project_id"], p["name"], p["path"]}), None)
        target_id = target_project["project_id"] if target_project else (target_value if target_value.startswith("external:") else f"external:{target_value}")
        edge = make_edge(
            project["project_id"], target_id, str(item.get("kind", item.get("relationship_type", "declared"))), str(item.get("strength", "required")), str(item.get("status", "declared")),
            evidence, {"manifest": evidence, "target": target_value}, projects, target_project,
        )
        if is_relationship or item.get("relationship_type"):
            edge["relationship_type"] = str(item.get("relationship_type", item.get("kind", "declared")))
            edge["relationship_source"] = "manifest"
        dependencies.append(edge)
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


def import_candidates(base: Path, specifier: str, extensions: tuple[str, ...]) -> list[Path]:
    target = base / specifier
    return [target, *[Path(f"{target}{extension}") for extension in extensions], *[target / f"index{extension}" for extension in extensions], target / "__init__.py"]


def resolve_import_path(source: Path, specifier: str, root: Path, language: str, go_module: str | None = None) -> Path | None:
    if language == "python":
        if specifier.startswith("."):
            dots = len(specifier) - len(specifier.lstrip("."))
            base = source.parent
            for _ in range(max(0, dots - 1)):
                base = base.parent
            module = specifier[dots:].replace(".", "/")
            candidates = import_candidates(base, module, (".py",))
        else:
            module = specifier.replace(".", "/")
            candidates = import_candidates(root, module, (".py",)) + import_candidates(root / "src", module, (".py",))
    elif language in {"javascript", "typescript"}:
        if not specifier.startswith("."):
            return None
        candidates = import_candidates(source.parent, specifier, (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
    elif language == "go":
        if not go_module or specifier != go_module and not specifier.startswith(f"{go_module}/"):
            return None
        suffix = specifier.removeprefix(go_module).lstrip("/")
        package_root = root / suffix
        if package_root.is_dir():
            return next((candidate.resolve() for candidate in sorted(package_root.glob("*.go")) if candidate.is_file()), None)
        return package_root.resolve() if package_root.is_file() else None
    elif language == "rust":
        if specifier.startswith("crate::"):
            module = specifier.removeprefix("crate::").replace("::", "/")
            candidates = import_candidates(root / "src", module, (".rs",))
        else:
            candidates = import_candidates(root / "src", specifier.replace("::", "/"), (".rs",))
    else:
        return None
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def package_name(specifier: str) -> str:
    if specifier.startswith("@"):
        return "/".join(specifier.split("/")[:2])
    return specifier.split("/")[0].split(".")[0]


def module_import_records(root: Path, workspace_root: Path, project: dict[str, Any], roots: list[Path], projects_by_root: dict[str, dict[str, Any]], projects: list[dict[str, Any]], package_index: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    patterns = {
        "python": [
            (re.compile(r"^\s*from\s+([.\w]+)\s+import\s+", re.MULTILINE), "static"),
            (re.compile(r"^\s*import\s+([A-Za-z_][\w., ]*)", re.MULTILINE), "static"),
        ],
        "javascript": [
            (re.compile(r"^\s*(?:import|export).*?from\s*[\"']([^\"']+)[\"']", re.MULTILINE), "static"),
            (re.compile(r"^\s*import\s*[\"']([^\"']+)[\"']", re.MULTILINE), "static"),
            (re.compile(r"\brequire\(\s*[\"']([^\"']+)[\"']\s*\)", re.MULTILINE), "static"),
            (re.compile(r"\bimport\(\s*[\"']([^\"']+)[\"']\s*\)", re.MULTILINE), "dynamic"),
            (re.compile(r"\bimport\(\s*([A-Za-z_$][\w$]*)\s*\)", re.MULTILINE), "dynamic_unresolved"),
        ],
        "go": [(re.compile(r"\bimport\s*(?:\(\s*(.*?)\)|[\"']([^\"']+)[\"'])", re.DOTALL), "static")],
        "rust": [
            (re.compile(r"^\s*use\s+([A-Za-z_][\w:]*)", re.MULTILINE), "static"),
            (re.compile(r"^\s*extern\s+crate\s+([A-Za-z_][\w]*)", re.MULTILINE), "static"),
            (re.compile(r"^\s*mod\s+([A-Za-z_][\w]*)\s*;", re.MULTILINE), "static"),
        ],
    }
    go_module = None
    go_mod = root / "go.mod"
    if go_mod.exists():
        module_match = re.search(r"^\s*module\s+(\S+)", read_text(go_mod), re.MULTILINE)
        go_module = module_match.group(1) if module_match else None
    for current, dirs, files in os.walk(root):
        dirs[:] = [directory for directory in dirs if directory not in DEFAULT_IGNORES and directory not in SCAN_SKIP_DIRS]
        for filename in files:
            path = Path(current) / filename
            suffix = path.suffix.lower()
            if suffix not in IMPORT_SOURCE_EXTENSIONS:
                continue
            language = "python" if suffix == ".py" else ("typescript" if suffix in {".ts", ".tsx"} else ("javascript" if suffix in {".js", ".jsx", ".mjs", ".cjs"} else ("rust" if suffix == ".rs" else suffix.lstrip("."))))
            text = read_text(path, 300_000)
            imports: list[tuple[str, str]] = []
            pattern_language = "javascript" if language == "typescript" else language
            for pattern, import_kind in patterns.get(pattern_language, []):
                for match in pattern.finditer(text):
                    values = [value for value in match.groups() if value]
                    if language == "python":
                        values = [re.split(r"\s+as\s+", part.strip(), maxsplit=1)[0].strip() for value in values for part in value.split(",") if part.strip()]
                    if language == "go" and values and "\n" in values[0]:
                        values = re.findall(r"[\"']([^\"']+)[\"']", values[0])
                    imports.extend((value, import_kind) for value in values)
            seen: set[tuple[str, str]] = set()
            for specifier, import_kind in imports:
                if (specifier, import_kind) in seen:
                    continue
                seen.add((specifier, import_kind))
                local_target = resolve_import_path(path, specifier, root, language, go_module)
                target_project = project_for_path(local_target, roots, projects_by_root) if local_target else None
                resolution = "local" if local_target else "unresolved"
                target_path = rel(target_project_root, local_target) if local_target and (target_project_root := next((candidate for candidate in roots if local_target == candidate.resolve() or candidate.resolve() in local_target.parents), None)) else None
                target_id = target_project["project_id"] if target_project else None
                if import_kind == "dynamic_unresolved":
                    target_id = "external:UNKNOWN"
                    resolution = "unresolved"
                elif not local_target and not specifier.startswith("."):
                    package = package_name(specifier)
                    target_id = package_index.get(package, f"external:{package}")
                    resolution = "workspace_package" if package in package_index else "external"
                if not target_id:
                    target_id = "external:UNKNOWN"
                evidence = rel(workspace_root, path)
                record = {
                    "import_id": stable_id("import", json.dumps([project["project_id"], evidence, specifier, import_kind], sort_keys=True)),
                    "source_project_id": project["project_id"],
                    "source_path": rel(root, path),
                    "language": language,
                    "specifier": specifier,
                    "kind": "dynamic_import" if import_kind in {"dynamic", "dynamic_unresolved"} else "module_import",
                    "resolution": resolution,
                    "target_project_id": target_id,
                    "target_path": target_path,
                    "evidence": [evidence],
                }
                records.append(record)
                if resolution != "local" and target_id != project["project_id"]:
                    target = next((item for item in projects if item["project_id"] == target_id), None)
                    edge = make_edge(project["project_id"], target_id, "module_import", "required", "active", evidence, {"specifier": specifier, "language": language, "resolution": resolution}, projects, target)
                    edge["confidence"] = "medium" if resolution in {"external", "unresolved"} else "high"
                    edges.append(edge)
    return sorted(records, key=lambda item: item["import_id"]), sorted(edges, key=lambda item: item["dependency_id"])


def source_truth_rules(projects: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return explicitly configured source-of-truth rules.

    The public core does not ship portfolio-specific rules. Projects can add
    rules through a future adapter or an explicit registry manifest.
    """
    return []


def findings(projects: list[dict[str, Any]], artifacts: list[dict[str, Any]], dependencies: list[dict[str, Any]], excluded: list[dict[str, Any]], source_of_truth: list[dict[str, Any]] | None = None, raw_dependencies: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    project_ids = {item["project_id"] for item in projects}
    for edge in dependencies:
        if edge["target"]["project_id"].startswith("external:") or edge["target"]["project_id"] not in project_ids:
            result.append({"finding_id": "DEP-001", "severity": "info", "category": "dependency", "status": "unknown", "subject": edge["dependency_id"], "message": f"Dependency provider is external or unresolved: {edge['target']['project_id']}", "evidence": edge["evidence"]})
    sot_by_domain: dict[str, list[dict[str, Any]]] = {}
    for rule in source_of_truth or []:
        sot_by_domain.setdefault(str(rule.get("domain", "UNKNOWN")), []).append(rule)
    for domain, rules in sorted(sot_by_domain.items()):
        authorities = {json.dumps(rule.get("authority", {}), ensure_ascii=False, sort_keys=True) for rule in rules}
        if len(authorities) > 1:
            result.append({"finding_id": "SOT-001", "severity": "medium", "category": "source_of_truth", "status": "conflict", "subject": domain, "message": f"Multiple authorities are declared for source-of-truth domain: {domain}", "evidence": sorted({evidence for rule in rules for evidence in rule.get("evidence", [])})})
    edge_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for edge in raw_dependencies or []:
        key = (edge["source"]["project_id"], edge["target"]["project_id"], edge["kind"])
        edge_groups.setdefault(key, []).append(edge)
    for key, edges in sorted(edge_groups.items()):
        states = {(edge.get("status"), edge.get("strength")) for edge in edges}
        if len(states) > 1:
            result.append({"finding_id": "REL-002", "severity": "medium", "category": "dependency", "status": "conflict", "subject": ":".join(key), "message": "Contradictory status or strength declarations exist for the same dependency edge", "evidence": sorted({evidence for edge in edges for evidence in edge.get("evidence", [])})})
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
    root_ids = root_identifiers(roots)
    root_records = [{"root_id": root_ids[str(root)], "name": root.name, "path": rel(portfolio_parent, root) or ".", "local_path": str(root), "trust_boundary": "local_workspace", "deployment_boundary": "UNKNOWN", "git_boundary": "independent_git_roots"} for root in roots]
    all_projects: list[Path] = []; excluded_paths: list[Path] = []
    root_for_project: dict[str, Path] = {}
    for workspace_root in roots:
        found, excluded = discover_roots(workspace_root, excluded_names)
        all_projects.extend(found); excluded_paths.extend(excluded)
        for item in found: root_for_project[str(item)] = workspace_root
    all_projects = sorted(set(all_projects), key=lambda p: (len(p.parts), p.as_posix()))
    repo_records: dict[str, dict[str, Any]] = {}; projects_by_root: dict[str, dict[str, Any]] = {}
    for item in all_projects:
        workspace_root = root_for_project[str(item)]; rid = root_ids[str(workspace_root)]; path = rel(workspace_root, item)
        checkout_id = f"checkout.{rid}.{path.replace('/', '.') }".rstrip(".")
        gm = git_metadata(item); remote = gm["remote"]
        repository_id = f"repo.{hashlib.sha1(remote.encode()).hexdigest()[:12]}" if remote != "UNKNOWN" else f"repo.{rid}.{path.replace('/', '.') }".rstrip(".")
        repo_records[checkout_id] = {"repository_id": repository_id, "git": gm}
    for item in all_projects:
        workspace_root = root_for_project[str(item)]
        record = project_record(item, portfolio_parent, workspace_root, repo_records, root_ids[str(workspace_root)])
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
    merged_artifacts: dict[tuple[str, str], dict[str, Any]] = {
        (item["project_id"], item["path"]): item for item in artifacts
    }
    # Explicit project metadata is authoritative when it describes a path
    # already recognized by a built-in adapter.
    for item in manifest_artifacts:
        merged_artifacts[(item["project_id"], item["path"])] = item
    artifacts = list(merged_artifacts.values())
    package_index: dict[str, str] = {}
    for item in active_root_paths:
        p = projects_by_root[str(item)]
        for manifest in all_package_manifests(item):
            try:
                name = json.loads(read_text(manifest)).get("name")
                if name: package_index[name] = p["project_id"]
            except json.JSONDecodeError: pass
    imports: list[dict[str, Any]] = []
    import_dependencies: list[dict[str, Any]] = []
    for item in active_root_paths:
        project = projects_by_root[str(item)]
        discovered_imports, discovered_edges = module_import_records(item, root_for_project[str(item)], project, active_root_paths, projects_by_root, projects, package_index)
        imports.extend(discovered_imports)
        import_dependencies.extend(discovered_edges)
    dependencies: list[dict[str, Any]] = []
    for item in active_root_paths:
        p = projects_by_root[str(item)]; workspace_root = root_for_project[str(item)]
        dependencies.extend(package_edges(item, portfolio_parent, workspace_root, p["project_id"], projects, package_index))
        dependencies.extend(text_edges(item, workspace_root, p["project_id"], active_root_paths, projects_by_root, projects))
    dependencies.extend(manifest_dependencies)
    dependencies.extend(import_dependencies)
    raw_dependencies = list(dependencies)
    grouped: dict[str, dict[str, Any]] = {}
    for edge in dependencies:
        grouping_key = json.dumps([edge["source"], edge["target"], edge["kind"], edge.get("reference", {})], ensure_ascii=False, sort_keys=True)
        if grouping_key not in grouped:
            grouped[grouping_key] = dict(edge)
            grouped[grouping_key]["dependency_id"] = stable_id("dep", grouping_key)
        else:
            grouped[grouping_key]["evidence"] = sorted(set(grouped[grouping_key]["evidence"] + edge["evidence"]))[:100]
            if edge.get("relationship_source") == "manifest":
                grouped[grouping_key]["relationship_source"] = "manifest"
                if edge.get("relationship_type"):
                    grouped[grouping_key]["relationship_type"] = edge["relationship_type"]
    dependencies = sorted(grouped.values(), key=lambda e: e["dependency_id"])
    relationships = sorted((edge for edge in dependencies if edge.get("relationship_source") == "manifest"), key=lambda e: e["dependency_id"])
    excluded = [{"name": Path(path).name, "path": str(path), "reason": "excluded_from_active_registry"} for path in sorted(set(excluded_paths))]
    root_data = [{k: v for k, v in item.items() if k != "local_path"} for item in root_records]
    snapshot: dict[str, Any] = {
        "schema": SCHEMA, "snapshot_id": None,
        "portfolio": {"portfolio_id": "default.portfolio", "name": "Default Portfolio", "workspace_roots": root_data},
        "workspace": {"roots": root_data, "root": root_data[0]["path"] if len(root_data) == 1 else None, "observed_at": datetime.now(timezone.utc).isoformat()},
        "repositories": sorted(repos.values(), key=lambda r: r["repository_id"]), "checkouts": sorted(checkouts, key=lambda c: c["checkout_id"]),
        "projects": projects, "artifacts": sorted(artifacts, key=lambda a: a["artifact_id"]), "dependencies": dependencies, "relationships": relationships, "imports": sorted(imports, key=lambda item: item["import_id"]),
        "source_of_truth": source_truth_rules(projects, artifacts) + manifest_source_truth, "findings": [], "exclusions": sorted(DEFAULT_IGNORES), "excluded_projects": excluded,
        "_local_roots": [{"root_id": item["root_id"], "local_path": item["local_path"]} for item in root_records],
    }
    snapshot["findings"] = findings(projects, artifacts, dependencies, excluded, snapshot["source_of_truth"], raw_dependencies)
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


def snapshot_validation_errors(snapshot: dict[str, Any]) -> list[str]:
    """Perform dependency-free structural checks for agent verification."""
    errors: list[str] = []
    required = {"schema", "snapshot_id", "portfolio", "projects", "repositories", "checkouts", "artifacts", "dependencies", "source_of_truth"}
    errors.extend(f"missing top-level field: {field}" for field in sorted(required - set(snapshot)))
    if snapshot.get("schema") != SCHEMA:
        errors.append(f"unsupported schema: {snapshot.get('schema', 'UNKNOWN')}")
    for collection in ("projects", "repositories", "checkouts", "artifacts", "dependencies", "source_of_truth", "imports"):
        if collection in snapshot and not isinstance(snapshot[collection], list):
            errors.append(f"collection is not an array: {collection}")
    for index, project in enumerate(snapshot.get("projects", [])):
        if not isinstance(project, dict) or not project.get("project_id"):
            errors.append(f"projects[{index}] is missing project_id")
    project_ids = [project.get("project_id") for project in snapshot.get("projects", []) if isinstance(project, dict)]
    if len(project_ids) != len(set(project_ids)):
        errors.append("projects contain duplicate project_id values")
    root_ids = [root.get("root_id") for root in snapshot.get("portfolio", {}).get("workspace_roots", []) if isinstance(root, dict)]
    if len(root_ids) != len(set(root_ids)):
        errors.append("portfolio workspace_roots contain duplicate root_id values")
    for index, artifact in enumerate(snapshot.get("artifacts", [])):
        if not isinstance(artifact, dict) or not artifact.get("artifact_id") or not artifact.get("project_id"):
            errors.append(f"artifacts[{index}] is missing artifact_id or project_id")
    valid_scopes = {"intra_root", "cross_root", "external", "unknown"}
    for collection in ("dependencies", "relationships"):
        for index, edge in enumerate(snapshot.get(collection, [])):
            if not isinstance(edge, dict) or not edge.get("dependency_id") or not edge.get("source") or not edge.get("target"):
                errors.append(f"{collection}[{index}] is missing edge identity or endpoints")
            elif edge.get("scope") not in valid_scopes:
                errors.append(f"{collection}[{index}] has invalid scope: {edge.get('scope', 'UNKNOWN')}")
    for index, item in enumerate(snapshot.get("imports", [])):
        required_import_fields = ("import_id", "source_project_id", "source_path", "language", "specifier", "kind", "resolution", "evidence")
        if not isinstance(item, dict) or any(not item.get(field) for field in required_import_fields):
            errors.append(f"imports[{index}] is missing required import metadata")
    if not no_absolute_paths(snapshot):
        errors.append("snapshot contains an absolute local path")
    return errors


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


def git_change_set(snapshot: dict[str, Any], roots: list[Path], mode: str, base: str | None = None) -> tuple[list[str], list[dict[str, str]]]:
    """Read changed paths from each discovered Git checkout."""
    changes: list[str] = []
    sources: list[dict[str, str]] = []
    for project in snapshot["projects"]:
        workspace_root = next((root for root in roots if root_id_for(root) == project["root_id"]), None)
        if workspace_root is None:
            continue
        project_root = workspace_root / project["path"] if project["path"] != "." else workspace_root
        if mode == "staged":
            args = ("diff", "--cached", "--name-only", "--diff-filter=ACDMRT")
            source_name = "git_index"
        elif mode == "base":
            args = ("diff", f"{base}...HEAD", "--name-only", "--diff-filter=ACDMRT")
            source_name = f"git_base:{base}"
        else:
            args = ("diff", "HEAD", "--name-only", "--diff-filter=ACDMRT")
            source_name = "git_worktree"
        output = run_git(project_root, *args)
        for changed in output.splitlines():
            changed = changed.strip()
            if not changed:
                continue
            workspace_path = f"{project['path']}/{changed}" if project["path"] != "." else changed
            if workspace_path not in changes:
                changes.append(workspace_path)
            sources.append({"project_id": project["project_id"], "path": workspace_path, "source": source_name})
    return sorted(changes), sorted(sources, key=lambda item: (item["project_id"], item["path"]))


RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def risk_report(projects: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    highest = "low"
    approval_required = False
    for artifact in artifacts:
        level = str(artifact.get("risk", "low")).lower()
        if level not in RISK_ORDER:
            level = "medium"
        if RISK_ORDER[level] > RISK_ORDER[highest]:
            highest = level
        requires_approval = bool(artifact.get("approval_required", False)) or level in {"high", "critical"}
        approval_required = approval_required or requires_approval
        if requires_approval:
            signals.append({"type": "artifact", "artifact_id": artifact["artifact_id"], "risk": level, "reason": "declared artifact risk or approval requirement", "owner": next((p.get("owner", "UNKNOWN") for p in projects if p["project_id"] == artifact["project_id"]), "UNKNOWN")})
    for project in projects:
        project_risk = str(project.get("risk", {}).get("default", "low")).lower()
        if project_risk not in RISK_ORDER:
            project_risk = "medium"
        if RISK_ORDER[project_risk] > RISK_ORDER[highest]:
            highest = project_risk
        if project.get("approval_required"):
            approval_required = True
            signals.append({"type": "project", "project_id": project["project_id"], "risk": project_risk, "reason": "project approval_required is true", "owner": project.get("owner", "UNKNOWN")})
    return {"level": highest, "approval_required": approval_required, "signals": signals}


def markdown_preflight(report: dict[str, Any]) -> str:
    lines = ["# AINE Preflight Report", "", f"- Evidence ID: `{report['evidence']['evidence_id']}`", f"- Read-only: `{str(report['read_only']).lower()}`", f"- Risk: **{report['risk']['level']}**", f"- Approval required: `{str(report['risk']['approval_required']).lower()}`", ""]
    lines.append("## Changes")
    if report["changes"]:
        lines.extend(f"- `{change}`" for change in report["changes"])
    else:
        lines.append("- None")
    lines.extend(["", "## Affected Projects"])
    if report["affected_projects"]:
        lines.extend(f"- `{project['project_id']}` (owner: `{project.get('owner', 'UNKNOWN')}`)" for project in report["affected_projects"])
    else:
        lines.append("- None")
    lines.extend(["", "## Required Validation"])
    if report["required_validation"]:
        lines.extend(f"- `{item['project_id']}` — `{item['check']}` ({item['command']})" for item in report["required_validation"])
    else:
        lines.append("- None discovered")
    lines.extend(["", "## Human Review Signals"])
    if report["risk"]["signals"]:
        lines.extend(f"- `{signal.get('type')}` `{signal.get('artifact_id', signal.get('project_id', 'UNKNOWN'))}` — {signal['reason']} (owner: `{signal.get('owner', 'UNKNOWN')}`)" for signal in report["risk"]["signals"])
    else:
        lines.append("- None")
    lines.extend(["", "## Unknowns"])
    if report["unknowns"]:
        lines.extend(f"- `{finding.get('finding_id', 'UNKNOWN')}` — {finding.get('message', 'UNKNOWN')}" for finding in report["unknowns"])
    else:
        lines.append("- None")
    lines.extend(["", "## Policy"])
    lines.append(f"- Mode: **{report['policy'].get('mode', 'advisory')}**")
    lines.append(f"- Status: **{report['policy']['status']}**")
    lines.append(f"- Enforced failure: **{str(report['policy'].get('enforced_failure', False)).lower()}**")
    for check in report["policy"]["checks"]:
        lines.append(f"- `{check['project_id']}` `{check['rule']}` — **{check['status']}**: {check['message']}")
    return "\n".join(lines) + "\n"


def evidence_record(report: dict[str, Any]) -> dict[str, Any]:
    """Return a report that is also directly consumable as aine.evidence.v1."""
    record = dict(report)
    record.update(report["evidence"])
    return record


EVIDENCE_STORE_SCHEMAS = {"aine.evidence.v1", "aine.handoff.v1", "aine.approval.v1", "aine.registry.v1"}


def record_digest(record: dict[str, Any]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def load_store_record(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("record"), dict) or not payload.get("record_id"):
        raise ValueError("invalid evidence store envelope")
    expected = record_digest(payload["record"])
    if payload["record_id"] != expected:
        raise ValueError("evidence store integrity check failed")
    return payload


def store_record(store_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    schema = record.get("schema")
    if schema not in EVIDENCE_STORE_SCHEMAS:
        raise ValueError(f"unsupported record schema: {schema or 'UNKNOWN'}")
    digest = record_digest(record)
    store_root.expanduser().mkdir(parents=True, exist_ok=True)
    target = store_root.expanduser() / f"{digest.removeprefix('sha256:')}.json"
    envelope = {"record_id": digest, "record": record}
    encoded = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists():
        existing = load_store_record(target)
        if existing["record_id"] != digest:
            raise ValueError("evidence store collision detected")
        return {"record_id": digest, "schema": schema, "status": "already_present"}
    target.write_text(encoded, encoding="utf-8")
    return {"record_id": digest, "schema": schema, "status": "stored"}


def list_store_records(store_root: Path) -> list[dict[str, Any]]:
    if not store_root.expanduser().is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(store_root.expanduser().glob("*.json")):
        try:
            envelope = load_store_record(path)
            record = envelope["record"]
            records.append({"record_id": envelope["record_id"], "schema": record.get("schema", "UNKNOWN"), "path": path.name})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            records.append({"path": path.name, "status": "invalid", "error": str(exc)})
    return records


def command_evidence(args: argparse.Namespace) -> int:
    store_root = Path(args.store).expanduser().resolve()
    if args.evidence_action == "store":
        try:
            input_path = Path(args.input).expanduser()
            source = json.loads(input_path.read_text(encoding="utf-8"))
            nested_evidence = source.get("evidence") if isinstance(source, dict) else None
            record = evidence_record(source) if isinstance(nested_evidence, dict) and nested_evidence.get("schema") == "aine.evidence.v1" and source.get("schema") != "aine.evidence.v1" else source
            if not isinstance(record, dict):
                raise ValueError("input record must be a JSON object")
            print_json(store_record(store_root, record))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"could not store evidence: {exc}", file=sys.stderr); return 2
    elif args.evidence_action == "list":
        print_json(list_store_records(store_root))
    elif args.evidence_action == "get":
        record_id = args.id if args.id.startswith("sha256:") else f"sha256:{args.id}"
        target = store_root / f"{record_id.removeprefix('sha256:')}.json"
        try:
            print_json(load_store_record(target)["record"])
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"could not read evidence: {exc}", file=sys.stderr); return 2
    return 0


def authorization_value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        expected_values = {str(item) for item in expected}
        if isinstance(actual, list):
            return bool(expected_values.intersection(str(item) for item in actual))
        return str(actual) in expected_values
    if isinstance(actual, list):
        return str(expected) in {str(item) for item in actual}
    return actual == expected or str(actual) == str(expected)


def authorization_lookup(context: dict[str, Any], path: str) -> Any:
    value: Any = context
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def evaluate_authorization(projects: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for project in projects:
        policy = project.get("policy", {})
        if not isinstance(policy, dict):
            continue
        authorization = policy.get("authorization", {})
        rules = authorization.get("rules", []) if isinstance(authorization, dict) else []
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            roles = rule.get("roles", [])
            if roles and not set(str(role) for role in roles).intersection(context.get("subject", {}).get("roles", [])):
                continue
            teams = rule.get("teams", [])
            subject_attributes = context.get("subject", {}).get("attributes", {})
            subject_teams = set(str(item) for item in context.get("subject", {}).get("teams", []))
            if subject_attributes.get("team"):
                subject_teams.add(str(subject_attributes["team"]))
            if teams and not set(str(team) for team in teams).intersection(subject_teams):
                continue
            actions = rule.get("actions", [])
            if actions and context.get("action") not in {str(action) for action in actions}:
                continue
            if rule.get("requires_ownership"):
                resource = context.get("resource", {})
                owner_teams = {str(item) for item in resource.get("owner_teams", [])}
                delegate_teams = {str(item) for item in resource.get("delegate_teams", [])}
                delegated_by = str(context.get("delegation", {}).get("delegated_by", ""))
                if not subject_teams.intersection(owner_teams) and not delegated_by in delegate_teams:
                    continue
            conditions = rule.get("conditions", {})
            if not isinstance(conditions, dict) or not all(authorization_value_matches(authorization_lookup(context, str(path)), expected) for path, expected in conditions.items()):
                continue
            effect = str(rule.get("effect", "review_required"))
            status = "fail" if effect == "deny" else ("pass" if effect == "allow" else "review_required")
            decisions.append({"project_id": project["project_id"], "rule_id": str(rule.get("id", "UNKNOWN")), "effect": effect, "status": status, "evidence": rule.get("evidence", [])})
    statuses = {item["status"] for item in decisions}
    status = "fail" if "fail" in statuses else ("review_required" if "review_required" in statuses else ("pass" if "pass" in statuses else "not_configured"))
    return {"status": status, "context": context, "decisions": decisions}


def evaluate_policy(projects: list[dict[str, Any]], validation: list[dict[str, Any]], unresolved_changes: list[str], risk: dict[str, Any], unknowns: list[dict[str, Any]] | None = None, mode_override: str | None = None, authorization_context: dict[str, Any] | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    modes: set[str] = set()
    for project in projects:
        policy = project.get("policy", {})
        if not isinstance(policy, dict):
            continue
        project_id = project["project_id"]
        declared_mode = str(policy.get("mode", "advisory")).lower()
        effective_mode = mode_override or declared_mode
        if effective_mode not in {"advisory", "enforced"}:
            checks.append({"project_id": project_id, "rule": "policy_mode", "status": "fail", "message": f"unsupported policy mode: {effective_mode}"})
            effective_mode = "advisory"
        modes.add(effective_mode)
        required_risks = {str(item).lower() for item in policy.get("require_approval_for", [])}
        if required_risks and risk["level"] in required_risks:
            checks.append({"project_id": project_id, "rule": "require_approval_for", "status": "review_required", "message": f"risk level {risk['level']} requires human approval"})
        if policy.get("deny_unknown_changes") and unresolved_changes:
            checks.append({"project_id": project_id, "rule": "deny_unknown_changes", "status": "fail", "message": "one or more changed paths are outside the registered boundary"})
        required_checks = {str(item) for item in policy.get("required_checks", [])}
        available_checks = {item["check"] for item in validation if item["project_id"] == project_id}
        for required_check in sorted(required_checks - available_checks):
            checks.append({"project_id": project_id, "rule": "required_checks", "status": "fail", "message": f"required validation is not discoverable: {required_check}"})
        if not required_checks and not (required_risks and risk["level"] in required_risks) and not (policy.get("deny_unknown_changes") and unresolved_changes):
            checks.append({"project_id": project_id, "rule": "policy", "status": "pass", "message": "no declared policy violation"})
    if unresolved_changes and mode_override == "enforced" and not any(check["rule"] == "deny_unknown_changes" for check in checks):
        checks.append({"project_id": "UNKNOWN", "rule": "deny_unknown_changes", "status": "fail", "message": "enforced policy cannot pass changes outside the registered boundary"})
    authorization = evaluate_authorization(projects, authorization_context or {"subject": {"id": "anonymous", "roles": [], "attributes": {}}, "action": "preflight", "resource": {"type": "change_set", "risk": risk.get("level")}, "context": {"evidence_status": "unknown"}})
    if authorization["status"] in {"fail", "review_required"}:
        checks.append({"project_id": "AUTHORIZATION", "rule": "authorization", "status": authorization["status"], "message": f"authorization decision: {authorization['status']}"})
    statuses = {check["status"] for check in checks}
    status = "fail" if "fail" in statuses else ("review_required" if "review_required" in statuses else "pass")
    mode = "enforced" if "enforced" in modes else (mode_override or "advisory")
    evidence = {
        "unresolved_changes": list(unresolved_changes),
        "finding_ids": [item.get("finding_id", "UNKNOWN") for item in (unknowns or [])],
    }
    enforced_failure = mode == "enforced" and status != "pass"
    return {
        "mode": mode,
        "status": status,
        "checks": checks,
        "evidence": evidence,
        "enforced_failure": enforced_failure,
        "exit_code": 1 if enforced_failure else 0,
        "advisory_only": mode != "enforced",
        "authorization": authorization,
    }


def handoff_from_preflight(report: dict[str, Any]) -> dict[str, Any]:
    policy = report.get("policy", {"mode": "advisory", "status": "pass", "enforced_failure": False})
    requires_review = report["risk"]["approval_required"] or bool(report["unknowns"]) or policy["status"] != "pass" or policy.get("enforced_failure", False)
    return {
        "schema": "aine.handoff.v1",
        "handoff_id": stable_id("handoff", report["evidence"]["evidence_id"]),
        "evidence_id": report["evidence"]["evidence_id"],
        "status": "human_review_required" if requires_review else "ready_for_review",
        "changes": report["changes"],
        "affected_projects": [{"project_id": p["project_id"], "owner": p.get("owner", "UNKNOWN")} for p in report["affected_projects"]],
        "risk": report["risk"],
        "policy": policy,
        "approval": report.get("approval", approval_request_from_preflight(report)),
        "required_validation": report["required_validation"],
        "unknowns": report["unknowns"],
        "next_actions": ((["Enforced policy failed; review policy evidence", "Review risk and unknown relationships", "Run required validation"] if policy.get("enforced_failure", False) else ["Review risk and unknown relationships", "Run required validation"]) if requires_review else ["Run required validation", "Review and merge when checks pass"]),
        "read_only": True,
    }


def approval_request_from_preflight(report: dict[str, Any]) -> dict[str, Any]:
    policy = report.get("policy", {})
    authorization = policy.get("authorization", {})
    reasons: list[str] = []
    if report.get("risk", {}).get("approval_required"):
        reasons.append("risk or artifact policy requires human approval")
    if report.get("unknowns"):
        reasons.append("preflight contains unknowns requiring human review")
    if policy.get("status") == "review_required":
        reasons.append("policy evaluation requires human review")
    if authorization.get("status") == "review_required":
        reasons.append("authorization policy requires human review")
    blocked = policy.get("status") == "fail" or authorization.get("status") == "fail"
    required = bool(reasons)
    status = "blocked" if blocked else ("requested" if required else "not_required")
    decision = "denied" if blocked else ("pending" if required else "not_required")
    evidence_id = report.get("evidence", {}).get("evidence_id", "UNKNOWN")
    return {
        "schema": "aine.approval.v1",
        "approval_id": stable_id("approval", evidence_id),
        "evidence_id": evidence_id,
        "status": status,
        "decision": decision,
        "required": required,
        "reasons": reasons,
        "affected_projects": [project["project_id"] for project in report.get("affected_projects", [])],
        "read_only": True,
    }


def preflight(snapshot: dict[str, Any], changes: list[str], roots: list[Path], policy_mode: str | None = None, authorization_context: dict[str, Any] | None = None) -> dict[str, Any]:
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
    risk = risk_report(affected_projects, matched_artifacts)
    supplied_context = authorization_context or {}
    context = {
        "subject": supplied_context.get("subject", {"id": "anonymous", "roles": [], "attributes": {}}),
        "action": supplied_context.get("action", "preflight"),
        "resource": {
            "type": "change_set",
            "risk": risk.get("level"),
            "project_ids": [project["project_id"] for project in affected_projects],
            "owner_teams": sorted({project.get("ownership", {}).get("team", "UNKNOWN") for project in affected_projects}),
            "delegate_teams": sorted({delegate for project in affected_projects for delegate in project.get("ownership", {}).get("delegates", [])}),
            **supplied_context.get("resource", {}),
        },
        "context": {"evidence_status": "complete" if not unknowns else "unknown", **supplied_context.get("context", {})},
        "delegation": supplied_context.get("delegation", {}),
    }
    policy = evaluate_policy(affected_projects, validation, unresolved, risk, unknowns, policy_mode, context)
    report = {
        "changes": changes,
        "matched_projects": matched_projects,
        "matched_artifacts": matched_artifacts,
        "affected_projects": affected_projects,
        "impact": impact_reports,
        "source_of_truth": related_rules,
        "required_validation": validation,
        "policy": policy,
        "risk": risk,
        "unknowns": unknowns,
        "read_only": True,
        "snapshot_id": snapshot["snapshot_id"],
        "unresolved_changes": unresolved,
    }
    report["evidence"] = {
        "schema": "aine.evidence.v1",
        "evidence_id": stable_id("evidence", json.dumps([snapshot["snapshot_id"], changes, report["risk"]], ensure_ascii=False, sort_keys=True)),
        "kind": "preflight",
        "snapshot_id": snapshot["snapshot_id"],
        "claims": {
            "matched_projects": [p["project_id"] for p in matched_projects],
            "affected_projects": [p["project_id"] for p in affected_projects],
            "matched_artifacts": [a["artifact_id"] for a in matched_artifacts],
            "policy_status": policy["status"],
            "policy_mode": policy["mode"],
            "policy_enforced_failure": policy["enforced_failure"],
            "policy_evidence": policy["evidence"],
            "authorization": policy["authorization"],
        },
    }
    report["approval"] = approval_request_from_preflight(report)
    report["evidence"]["claims"]["approval"] = {
        "approval_id": report["approval"]["approval_id"],
        "status": report["approval"]["status"],
        "required": report["approval"]["required"],
    }
    return report


def command(args: argparse.Namespace) -> int:
    if args.action == "init":
        return write_local_config(args)
    if args.action == "portfolio" and args.portfolio_action == "list":
        snapshot = load_snapshot(args); print_json(snapshot["portfolio"]); return 0
    if args.action == "evidence":
        return command_evidence(args)
    if args.action == "handoff" and getattr(args, "preflight", None):
        try:
            report = json.loads(Path(args.preflight).expanduser().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"could not read preflight report: {exc}", file=sys.stderr); return 2
        handoff = handoff_from_preflight(report)
        if args.output:
            output = Path(args.output).expanduser().resolve(); output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(handoff, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print_json({"status": "written", "handoff_id": handoff["handoff_id"]})
        elif args.format == "markdown":
            print(f"# AINE Handoff\n\n- Handoff ID: `{handoff['handoff_id']}`\n- Status: **{handoff['status']}**\n- Evidence ID: `{handoff['evidence_id']}`\n\n## Next actions\n" + "\n".join(f"- {item}" for item in handoff["next_actions"]))
        else:
            print_json(handoff)
        return 0
    if args.action == "approval":
        try:
            record = json.loads(Path(args.handoff).expanduser().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"could not read handoff record: {exc}", file=sys.stderr); return 2
        approval = record.get("approval")
        if not isinstance(approval, dict):
            source = dict(record)
            source["evidence"] = {"evidence_id": record.get("evidence_id", "UNKNOWN")}
            approval = approval_request_from_preflight(source)
        if getattr(args, "decision", None):
            if not getattr(args, "decided_by", None):
                print("--decided-by is required when recording an external decision", file=sys.stderr); return 2
            approval = dict(approval)
            approval["status"] = args.decision
            approval["decision"] = args.decision
            approval["decided_by"] = args.decided_by
            approval["decision_source"] = "external_input"
        if args.output:
            output = Path(args.output).expanduser().resolve(); output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(approval, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print_json({"status": "written", "approval_id": approval["approval_id"]})
        else:
            print_json(approval)
        return 0
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
    elif action in {"imports", "import"}: print_json(snapshot.get("imports", []))
    elif action in {"relationships", "relationship"}:
        relationships = snapshot.get("relationships", [])
        if getattr(args, "project", None):
            relationships = [item for item in relationships if args.project in {item["source"]["project_id"], item["target"]["project_id"]}]
        if getattr(args, "relationship_type", None):
            relationships = [item for item in relationships if item.get("relationship_type") == args.relationship_type]
        if getattr(args, "relationship_status", None):
            relationships = [item for item in relationships if item.get("status") == args.relationship_status]
        print_json(relationships)
    elif action in {"dependency-graph", "graph"}: print_json({"projects": snapshot["projects"], "dependencies": snapshot["dependencies"]})
    elif action in {"findings"}: print_json(snapshot["findings"])
    elif action == "source-of-truth": print_json([r for r in snapshot["source_of_truth"] if args.domain in r["domain"]])
    elif action == "impact":
        seed = args.project or args.path or args.artifact
        if not seed: print("impact requires --project, --path, or --artifact", file=sys.stderr); return 2
        print_json(impact(snapshot, seed))
    elif action == "preflight":
        changes = list(args.change or [])
        change_source = "explicit"
        git_sources: list[dict[str, str]] = []
        if args.diff or args.staged or args.base:
            mode = "staged" if args.staged else ("base" if args.base else "diff")
            changes, git_sources = git_change_set(snapshot, roots, mode, args.base)
            change_source = mode
        if not changes: print("preflight requires at least one --change", file=sys.stderr); return 2
        attributes = {}
        for item in getattr(args, "attribute", []) or []:
            if "=" in item:
                key, value = item.split("=", 1)
                attributes[key] = value
        authorization_context = {
            "subject": {"id": getattr(args, "subject_id", None) or "anonymous", "roles": list(getattr(args, "role", []) or []), "teams": list(getattr(args, "team", []) or []), "attributes": attributes},
            "action": "preflight",
            "delegation": {"delegated_by": getattr(args, "delegated_by", None) or ""},
        }
        report = preflight(snapshot, changes, roots, getattr(args, "policy_mode", None), authorization_context)
        report["change_source"] = change_source
        report["git_sources"] = git_sources
        if args.output:
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            content = markdown_preflight(report) if args.format == "markdown" else json.dumps(evidence_record(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            output.write_text(content, encoding="utf-8")
            print_json({"status": "written", "format": args.format, "evidence_id": report["evidence"]["evidence_id"]})
            return report["policy"]["exit_code"]
        if args.format == "markdown":
            print(markdown_preflight(report), end="")
        else:
            print_json(report)
        return report["policy"]["exit_code"]
    elif action == "workspace": print_json(snapshot["portfolio"]["workspace_roots"])
    elif action == "context":
        selected = snapshot["projects"]
        if getattr(args, "project", None):
            selected = [item for item in selected if args.project in {item["project_id"], item["name"], item["path"]}]
        selected_ids = {item["project_id"] for item in selected}
        selected_artifacts = [item for item in snapshot["artifacts"] if item["project_id"] in selected_ids]
        selected_dependencies = [item for item in snapshot["dependencies"] if item["source"]["project_id"] in selected_ids or item["target"]["project_id"] in selected_ids]
        selected_relationships = [item for item in snapshot.get("relationships", []) if item["source"]["project_id"] in selected_ids or item["target"]["project_id"] in selected_ids]
        selected_imports = [item for item in snapshot.get("imports", []) if item["source_project_id"] in selected_ids or item.get("target_project_id") in selected_ids]
        selected_rules = [item for item in snapshot["source_of_truth"] if any(project_id in json.dumps(item, ensure_ascii=False) for project_id in selected_ids)]
        selected_findings = [item for item in snapshot["findings"] if any(project_id in json.dumps(item, ensure_ascii=False) for project_id in selected_ids)]
        print_json({"portfolio": snapshot["portfolio"], "projects": selected, "artifacts": selected_artifacts, "dependencies": selected_dependencies, "relationships": selected_relationships, "imports": selected_imports, "source_of_truth": selected_rules, "findings": selected_findings, "snapshot_id": snapshot["snapshot_id"]})
    elif action == "validate":
        errors = snapshot_validation_errors(snapshot)
        print_json({"valid": not errors, "snapshot_id": snapshot.get("snapshot_id"), "errors": errors, "findings": snapshot.get("findings", [])})
    elif action == "handoff":
        print_json({"portfolio_id": snapshot["portfolio"]["portfolio_id"], "snapshot_id": snapshot["snapshot_id"], "projects": [{"project_id": p["project_id"], "root_id": p["root_id"], "checkout_id": p["checkout_id"], "path": p["path"]} for p in snapshot["projects"]], "cross_root_dependencies": sum(e["scope"] == "cross_root" for e in snapshot["dependencies"])})
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
    for name in ("projects", "project", "repositories", "repo", "checkouts", "checkout", "artifacts", "artifact", "dependencies", "deps", "imports", "import", "relationships", "relationship", "dependency-graph", "graph", "findings", "workspace", "context", "validate", "handoff"):
        child = sub.add_parser(name)
        if name in {"context", "validate", "handoff", "workspace", "findings", "projects", "project", "repositories", "repo", "checkouts", "checkout", "artifacts", "artifact", "dependencies", "deps", "dependency", "imports", "import", "relationships", "relationship", "dependency-graph", "graph"}:
            add_workspace_options(child)
        if name in {"relationships", "relationship"}:
            child.add_argument("--project", help="filter relationships touching a project")
            child.add_argument("--relationship-type", help="filter by relationship_type")
            child.add_argument("--relationship-status", help="filter by lifecycle status")
        if name == "context":
            child.add_argument("--project", help="scope context to a project")
        if name == "handoff":
            child.add_argument("--preflight", help="read a saved preflight evidence report")
            child.add_argument("--format", choices=("json", "markdown"), default="json")
            child.add_argument("--output", help="write the handoff record")
        if name in {"project", "repo", "checkout", "artifact", "dependency", "workspace"}:
            child.add_argument("subcommand", nargs="?")
    approval = sub.add_parser("approval", help="emit a read-only approval request from a handoff")
    approval.add_argument("--handoff", required=True, help="read a handoff record")
    approval.add_argument("--output", help="write the approval request")
    approval.add_argument("--decision", choices=("approved", "rejected"), help="record an external decision without executing it")
    approval.add_argument("--decided-by", help="external subject that supplied the decision")
    evidence = sub.add_parser("evidence", help="store and retrieve local evidence records")
    evidence_sub = evidence.add_subparsers(dest="evidence_action", required=True)
    evidence_store = evidence_sub.add_parser("store", help="store a JSON record in the local append-only store")
    evidence_store.add_argument("--input", required=True, help="JSON evidence, handoff, approval, or registry record")
    evidence_store.add_argument("--store", required=True, help="local evidence store directory")
    evidence_list = evidence_sub.add_parser("list", help="list stored records")
    evidence_list.add_argument("--store", required=True, help="local evidence store directory")
    evidence_get = evidence_sub.add_parser("get", help="read and verify a stored record")
    evidence_get.add_argument("--id", required=True, help="record ID or digest")
    evidence_get.add_argument("--store", required=True, help="local evidence store directory")
    dependency = sub.add_parser("dependency"); dependency.add_argument("subcommand", nargs="?")
    sot = sub.add_parser("source-of-truth"); sot.add_argument("domain")
    impact_cmd = sub.add_parser("impact"); impact_cmd.add_argument("target", nargs="?"); impact_cmd.add_argument("--project"); impact_cmd.add_argument("--path"); impact_cmd.add_argument("--artifact"); add_workspace_options(impact_cmd)
    preflight_cmd = sub.add_parser("preflight", help="analyze a proposed change without mutating the workspace"); preflight_cmd.add_argument("--change", action="append", help="changed path, artifact, or project; repeat as needed"); preflight_cmd.add_argument("--diff", action="store_true", help="read staged and unstaged changes from Git"); preflight_cmd.add_argument("--staged", action="store_true", help="read staged changes from Git"); preflight_cmd.add_argument("--base", help="compare each checkout against BASE...HEAD"); preflight_cmd.add_argument("--format", choices=("json", "markdown"), default="json"); preflight_cmd.add_argument("--output", help="write the preflight evidence report"); preflight_cmd.add_argument("--policy-mode", choices=("advisory", "enforced"), help="override project policy mode for this preflight"); preflight_cmd.add_argument("--subject-id", help="portable subject identifier for policy evaluation"); preflight_cmd.add_argument("--role", action="append", help="subject role; repeat as needed"); preflight_cmd.add_argument("--team", action="append", help="subject team; repeat as needed"); preflight_cmd.add_argument("--delegated-by", help="team or owner delegating this action"); preflight_cmd.add_argument("--attribute", action="append", help="subject attribute as key=value; repeat as needed"); add_workspace_options(preflight_cmd)
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
