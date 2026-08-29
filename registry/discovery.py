"""Evidence-backed project, artifact, relationship, and import discovery."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .constants import *
    from .common import *
    from . import inventory as inventory_module
except ImportError:  # direct execution compatibility
    from constants import *
    from common import *
    import inventory as inventory_module

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


LEGACY_RELATIONSHIP_KEY_RE = re.compile(r"^depends_on\s*:", re.MULTILINE)


def declares_legacy_relationships(root: Path) -> bool:
    """Report whether the loose portfolio descriptor still declares edges.

    Relationships belong in the registry manifest, where they are schema-checked
    and resolved against discovered projects. A `depends_on:` block in
    `manifest.yaml` is read by nothing, so it drifts without anything noticing.
    Matching the key by line avoids taking on a YAML dependency for one check.
    """
    descriptor = root / "manifest.yaml"
    return descriptor.exists() and bool(LEGACY_RELATIONSHIP_KEY_RE.search(read_text(descriptor, 200_000)))


def owns_any_file(root: Path, roots: Sequence[Path], match: Callable[[str], bool]) -> bool:
    """Whether `root` itself contains a matching file, excluding nested checkouts."""
    return any(any(match(name) for name in files) for _current, _dirs, files in owned_walk(root, roots))


def runtime_metadata(root: Path, roots: Sequence[Path]) -> dict[str, Any]:
    languages: set[str] = set(); frameworks: set[str] = set(); entrypoints: list[str] = []
    if (root / "package.json").exists() or owns_any_file(root, roots, lambda name: name == "package.json"): languages.add("javascript/typescript")
    if (root / "Cargo.toml").exists(): languages.add("rust")
    if (root / "go.mod").exists(): languages.add("go")
    if (root / "pyproject.toml").exists() or owns_any_file(root, roots, lambda name: name.endswith(".py")): languages.add("python")
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


def project_record(root: Path, portfolio_root: Path, workspace_root: Path, repos: dict[str, dict[str, Any]], roots: Sequence[Path], workspace_root_id: str | None = None) -> dict[str, Any]:
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
        "git": repo["git"], "runtime": runtime_metadata(root, roots), "instructions": instructions,
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


def nested_root_relpaths(root: Path, roots: Sequence[Path]) -> set[str]:
    """Paths, relative to `root`, of the Git roots nested inside it.

    A checkout nested inside another checkout is a separate repository, so its
    files belong to it and not to the umbrella that happens to contain it. The
    full discovered-root list is used rather than the active subset: a Git
    boundary exists whether or not the child appears in the project graph.
    """
    base = root.resolve()
    nested: set[str] = set()
    for candidate in roots:
        resolved = candidate.resolve()
        if resolved != base and base in resolved.parents:
            nested.add(resolved.relative_to(base).as_posix())
    return nested


def owned_walk(root: Path, roots: Sequence[Path]) -> Iterator[tuple[str, list[str], list[str]]]:
    """Walk only the files `root` owns.

    Prunes the same ignored and skipped directories as before, and additionally
    prunes every nested Git root. Files the parent owns stay visible: only
    directories that are themselves checkout roots are removed.
    """
    nested = nested_root_relpaths(root, roots)
    for current, dirs, files in os.walk(root):
        prefix = Path(current).relative_to(root).as_posix()
        dirs[:] = [
            directory for directory in dirs
            if directory not in DEFAULT_IGNORES and directory not in SCAN_SKIP_DIRS
            and (directory if prefix == "." else f"{prefix}/{directory}") not in nested
        ]
        yield current, dirs, files


def artifact_records(root: Path, workspace_root: Path, pid: str, root_id: str, roots: Sequence[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for current, dirs, files in owned_walk(root, roots):
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


def project_manifest(project_root: Path) -> tuple[dict[str, Any], Path | None]:
    path = project_root / PROJECT_MANIFEST
    try:
        data = json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return {}, None
    return (data, path) if isinstance(data, dict) else ({}, None)


def declared_project_ids(projects: list[dict[str, Any]], project_roots: dict[str, Path]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Honor the `project.id` a manifest declares, as an alias only.

    A project ID is derived from the workspace-relative path, so it changes
    with the root topology a run is configured for, while a manifest declares
    a fixed identifier and names its peers by that identifier. Without the
    alias every declared relationship resolves to an external phantom whenever
    the two disagree. The alias never replaces the derived `project_id`:
    rewriting identity from a file inside the project would let a project
    rename itself, and every snapshot and evidence record already refers to
    the derived form.
    """
    # Every identifier `resolve_project_reference` already matches on, and who
    # owns it. An alias may not take a name a different project answers to.
    owners: dict[str, set[str]] = {}
    for item in projects:
        for key in (item["project_id"], item["name"], item["path"]):
            owners.setdefault(key, set()).add(item["project_id"])
    claims: dict[str, list[dict[str, Any]]] = {}
    for item in projects:
        root = project_roots.get(item["project_id"])
        if root is None:
            continue
        data, manifest_path = project_manifest(root)
        if manifest_path is None:
            continue
        metadata = data.get("project", {})
        declared = str(metadata.get("id", "")).strip() if isinstance(metadata, dict) else ""
        if not declared or declared == item["project_id"]:
            continue
        claims.setdefault(declared, []).append(item)
    aliases: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    for declared, claimants in sorted(claims.items()):
        reason = None
        if len(claimants) > 1:
            reason = "declared by more than one project"
        elif owners.get(declared, set()) - {claimants[0]["project_id"]}:
            reason = "shadows an identifier another project is already found by"
        if reason:
            # An unhonored claim stays off the project record: `declared_id`
            # means "this identifier resolves here", and the finding carries
            # the rejected claim for audit.
            conflicts.append({
                "declared_id": declared,
                "reason": reason,
                "projects": sorted(item["project_id"] for item in claimants),
                "manifests": sorted(f"{item['path']}/{PROJECT_MANIFEST.as_posix()}" for item in claimants),
            })
            continue
        aliases[declared] = claimants[0]["project_id"]
        claimants[0]["declared_id"] = declared
    return aliases, conflicts


def resolve_project_reference(value: str, projects: list[dict[str, Any]], aliases: dict[str, str]) -> dict[str, Any] | None:
    """Find the project a manifest or overlay names, by any identifier it may use."""
    aliased = aliases.get(value)
    if aliased:
        return next((p for p in projects if p["project_id"] == aliased), None)
    return next((p for p in projects if value in {p["project_id"], p["name"], p["path"]}), None)


def explicit_manifest_records(project: dict[str, Any], project_root: Path, workspace_root: Path, projects: list[dict[str, Any]], overlay_relationships: list[dict[str, Any]] | None = None, aliases: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Load project-owned explicit metadata without executing project code."""
    data, manifest_path = project_manifest(project_root)
    overlay_relationships = [item for item in (overlay_relationships or []) if isinstance(item, dict) and item.get("target")]
    has_manifest = bool(data) and manifest_path is not None
    if not has_manifest and not overlay_relationships:
        return [], [], []
    # An overlay carries relationships only. A project reached solely through
    # one declares no manifest, so no manifest-derived metadata is read here.
    evidence = f"{rel(workspace_root, project_root)}/{PROJECT_MANIFEST.as_posix()}"
    project_metadata = data.get("project", {})
    if has_manifest and isinstance(project_metadata, dict):
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
        declared_commands = project_metadata.get("commands", {})
        if isinstance(declared_commands, dict):
            commands = dict(project.get("commands", {}))
            for name, command in declared_commands.items():
                if isinstance(command, str):
                    commands[str(name)] = {"command": command, "evidence": evidence}
                elif isinstance(command, dict) and command.get("command"):
                    commands[str(name)] = {
                        **command,
                        "command": str(command["command"]),
                        "evidence": str(command.get("evidence", str(PROJECT_MANIFEST))),
                    }
            project["commands"] = commands
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
            # Recorded only for manifest-declared artifacts. Adapter-discovered
            # ones are found by walking the tree, so they exist by construction.
            "exists": path != "<local-path>" and (project_root / path).exists(),
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
    declared_relationships = (
        [(item, False, "manifest") for item in data.get("dependencies", [])]
        + [(item, True, "manifest") for item in data.get("relationships", [])]
        + [(item, True, "overlay") for item in overlay_relationships]
    )
    for item, is_relationship, declared_in in declared_relationships:
        if not isinstance(item, dict) or not item.get("target"):
            continue
        target_value = str(item["target"])
        target_project = resolve_project_reference(target_value, projects, aliases or {})
        target_id = target_project["project_id"] if target_project else (target_value if target_value.startswith("external:") else f"external:{target_value}")
        item_evidence = evidence if declared_in == "manifest" else OVERLAY_EVIDENCE
        reference = {declared_in: item_evidence, "target": target_value}
        edge = make_edge(
            project["project_id"], target_id, str(item.get("kind", item.get("relationship_type", "declared"))), str(item.get("strength", "required")), str(item.get("status", "declared")),
            item_evidence, reference, projects, target_project,
        )
        if is_relationship or item.get("relationship_type"):
            edge["relationship_type"] = str(item.get("relationship_type", item.get("kind", "declared")))
            edge["relationship_source"] = declared_in
            if "impact" in item:
                edge["impact"] = bool(item["impact"])
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


def text_edges(root: Path, workspace_root: Path, pid: str, roots: list[Path], projects_by_root: dict[str, dict[str, Any]], projects: list[dict[str, Any]], all_roots: Sequence[Path]) -> list[dict[str, Any]]:
    patterns = [
        (re.compile(r"(?:/[A-Za-z0-9_./~:@%+-]+|\.\./[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?)"), "filesystem"),
        (re.compile(r"(?:MCP_BASE_URL|MCP_SERVER_URL|API_BASE_URL|SERVICE_URL)"), "runtime_api"),
    ]
    seen: set[str] = set(); edges: list[dict[str, Any]] = []
    for current, dirs, files in owned_walk(root, all_roots):
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
            # A non-relative Python import also resolves against the directory of
            # the importing file when that file is run as a script, which is how
            # the `from .module import x` / `from module import x` fallback pair
            # is normally written. Sibling candidates come last so a project-root
            # or `src/` module still wins.
            candidates = import_candidates(root, module, (".py",)) + import_candidates(root / "src", module, (".py",)) + import_candidates(source.parent, module, (".py",))
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


def stdlib_package(specifier: str, language: str, go_module: str | None = None) -> str | None:
    """Return the standard-library provider for a specifier, or None.

    A standard-library import is resolved: the provider is the language runtime.
    Reporting it as an unknown external project would be a false unknown, so the
    import record keeps the specifier and no dependency edge is produced.
    """
    if language == "python":
        package = specifier.split(".")[0]
        return package if package in PYTHON_STDLIB_MODULES else None
    if language in {"javascript", "typescript"}:
        package = specifier.removeprefix("node:").split("/")[0]
        return package if package in NODE_BUILTIN_MODULES else None
    if language == "go":
        # A Go import path outside the standard library must start with a
        # domain-like element, so a first element without a dot is standard,
        # unless it belongs to a local module declared without a domain.
        head = specifier.split("/")[0]
        if go_module and (specifier == go_module or specifier.startswith(f"{go_module}/")):
            return None
        return specifier if head and "." not in head else None
    if language == "rust":
        crate = specifier.split("::")[0]
        return crate if crate in RUST_STDLIB_CRATES else None
    return None


def package_name(specifier: str) -> str:
    if specifier.startswith("@"):
        return "/".join(specifier.split("/")[:2])
    return specifier.split("/")[0].split(".")[0]


def module_import_records(root: Path, workspace_root: Path, project: dict[str, Any], roots: list[Path], projects_by_root: dict[str, dict[str, Any]], projects: list[dict[str, Any]], package_index: dict[str, str], all_roots: Sequence[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
    for current, dirs, files in owned_walk(root, all_roots):
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
                        values = [value for value in values if PYTHON_MODULE_RE.match(value)]
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
                    standard = stdlib_package(specifier, language, go_module)
                    if standard:
                        # Standard-library modules take precedence over an
                        # installed package of the same name at import time.
                        target_id = f"stdlib:{language}:{standard}"
                        resolution = "stdlib"
                    else:
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
                if resolution not in {"local", "stdlib"} and target_id != project["project_id"]:
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


def findings(projects: list[dict[str, Any]], artifacts: list[dict[str, Any]], dependencies: list[dict[str, Any]], excluded: list[dict[str, Any]], source_of_truth: list[dict[str, Any]] | None = None, raw_dependencies: list[dict[str, Any]] | None = None, alias_conflicts: list[dict[str, Any]] | None = None, published_ids: set[str] | None = None, legacy_relationship_manifests: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    project_ids = {item["project_id"] for item in projects}
    for edge in dependencies:
        if not (edge["target"]["project_id"].startswith("external:") or edge["target"]["project_id"] not in project_ids):
            continue
        # A hand-written declaration whose target no project answers to is a
        # broken reference, not an external provider. Resolution coerces it to
        # `external:` so the edge stays recordable, but reporting that as
        # DEP-001 info would bless the misclassification: a typo in `target`
        # would read as a legitimate third-party provider, at the severity of
        # an ordinary npm import, and never surface. An intentional external
        # provider says so with an explicit `external:` prefix, which is
        # exactly the case DEP-001 keeps. Derived edges (imports, packages,
        # path references) carry no declared target and also stay with DEP-001.
        reference = edge.get("reference", {})
        declared = reference.get("target")
        if isinstance(declared, str) and not declared.startswith("external:") and (reference.get("manifest") or reference.get("overlay")):
            result.append({"finding_id": "REL-004", "severity": "medium", "category": "dependency", "status": "dangling", "subject": edge["dependency_id"], "message": f"A declared edge names a target that no project answers to: {declared}. Fix the reference, or declare an intentional third-party provider as external:{declared}.", "evidence": edge["evidence"]})
        else:
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
    # A manifest is committed with its project, so a published project publishes
    # every target its manifest names. An overlay-declared edge cannot reach here:
    # it carries no `reference.manifest`, which is the point of declaring it there.
    for edge in dependencies if published_ids else []:
        if not edge.get("reference", {}).get("manifest"):
            continue
        target = edge["target"]["project_id"]
        if edge["source"]["project_id"] not in published_ids or target not in project_ids or target in published_ids:
            continue
        result.append({"finding_id": "PRJ-002", "severity": "high", "category": "disclosure", "status": "exposed", "subject": edge["dependency_id"], "message": f"A published project's manifest names an unpublished project: {target}. Declare the edge as a relationship overlay instead.", "evidence": edge["evidence"]})
    # A manifest that declares a file present when it is not makes every claim
    # hanging off it void: a source-of-truth authority with no authority, a
    # high-risk path guarding nothing, an approval gate on an absent file.
    for item in artifacts:
        if item.get("status") == "present" and item.get("exists") is False:
            result.append({"finding_id": "ART-001", "severity": "high", "category": "artifact", "status": "missing", "subject": item["artifact_id"], "message": f"A manifest declares an artifact as present that does not exist: {item['workspace_path']}", "evidence": item["evidence"]})
    for item in legacy_relationship_manifests or []:
        result.append({"finding_id": "REL-003", "severity": "medium", "category": "dependency", "status": "unmanaged", "subject": item["project_id"], "message": "Relationships are declared in manifest.yaml, where nothing reads or resolves them. Move them to the registry manifest.", "evidence": [item["evidence"]]})
    for conflict in alias_conflicts or []:
        result.append({"finding_id": "PRJ-001", "severity": "medium", "category": "identity", "status": "conflict", "subject": conflict["declared_id"], "message": f"Declared project id is not honored because it {conflict['reason']}: {', '.join(conflict['projects'])}", "evidence": conflict["manifests"]})
    if excluded:
        result.append({"finding_id": "SCOPE-001", "severity": "info", "category": "scope", "status": "declared", "subject": "portfolio.exclusions", "message": "Projects explicitly excluded from the active registry scope are preserved as exclusions.", "evidence": [item["path"] for item in excluded]})
    return result


def discover(workspace_roots: list[Path], excluded_names: set[str] | None = None, relationship_overlays: list[dict[str, Any]] | None = None, published_projects: list[str] | None = None, inventory: dict[str, Any] | None = None) -> dict[str, Any]:
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
        record = project_record(item, portfolio_parent, workspace_root, repo_records, all_projects, root_ids[str(workspace_root)])
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
    project_roots = {projects_by_root[str(path)]["project_id"]: path for path in active_root_paths}
    aliases, alias_conflicts = declared_project_ids(projects, project_roots)
    overlays_by_project: dict[str, list[dict[str, Any]]] = {}
    for overlay in relationship_overlays or []:
        if not isinstance(overlay, dict):
            continue
        declared_for = str(overlay.get("project", ""))
        owner = resolve_project_reference(declared_for, projects, aliases)
        if owner is None:
            continue
        overlays_by_project.setdefault(owner["project_id"], []).extend(
            item for item in overlay.get("relationships", []) if isinstance(item, dict) and item.get("target")
        )
    published_ids = {
        owner["project_id"]
        for owner in (resolve_project_reference(str(item), projects, aliases) for item in published_projects or [])
        if owner is not None
    }
    legacy_relationship_manifests = [
        {"project_id": pid, "evidence": f"{projects_by_root[str(path)]['path']}/manifest.yaml" if projects_by_root[str(path)]["path"] != "." else "manifest.yaml"}
        for pid, path in sorted(project_roots.items())
        if declares_legacy_relationships(path)
    ]
    artifacts: list[dict[str, Any]] = []
    manifest_records: list[tuple[dict[str, Any], Path, Path]] = []
    for item in active_root_paths:
        p = projects_by_root[str(item)]; artifacts.extend(artifact_records(item, root_for_project[str(item)], p["project_id"], p["root_id"], all_projects))
        manifest_records.append((p, item, root_for_project[str(item)]))
    manifest_artifacts: list[dict[str, Any]] = []
    manifest_dependencies: list[dict[str, Any]] = []
    manifest_source_truth: list[dict[str, Any]] = []
    for project, project_root, workspace_root in manifest_records:
        explicit_artifacts, explicit_dependencies, explicit_source_truth = explicit_manifest_records(project, project_root, workspace_root, projects, overlays_by_project.get(project["project_id"]), aliases)
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
        discovered_imports, discovered_edges = module_import_records(item, root_for_project[str(item)], project, active_root_paths, projects_by_root, projects, package_index, all_projects)
        imports.extend(discovered_imports)
        import_dependencies.extend(discovered_edges)
    dependencies: list[dict[str, Any]] = []
    for item in active_root_paths:
        p = projects_by_root[str(item)]; workspace_root = root_for_project[str(item)]
        dependencies.extend(package_edges(item, portfolio_parent, workspace_root, p["project_id"], projects, package_index))
        dependencies.extend(text_edges(item, workspace_root, p["project_id"], active_root_paths, projects_by_root, projects, all_projects))
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
            if edge.get("relationship_source") in RELATIONSHIP_SOURCES:
                grouped[grouping_key]["relationship_source"] = edge["relationship_source"]
                if edge.get("relationship_type"):
                    grouped[grouping_key]["relationship_type"] = edge["relationship_type"]
    dependencies = sorted(grouped.values(), key=lambda e: e["dependency_id"])
    relationships = sorted((edge for edge in dependencies if edge.get("relationship_source") in RELATIONSHIP_SOURCES), key=lambda e: e["dependency_id"])
    excluded = [{"name": Path(path).name, "path": str(path), "reason": "excluded_from_active_registry"} for path in sorted(set(excluded_paths))]
    inventory_block: dict[str, Any] | None = None
    inventory_findings: list[dict[str, Any]] = []
    if inventory is not None:
        # Lifecycle is a portfolio decision, not a filesystem fact. The join
        # records what the inventory says and reports where the two disagree; it
        # never invents a classification for a checkout the inventory omits.
        joined = inventory_module.classifications(inventory, roots)
        base, index = joined["base"], joined["index"]
        discovered: dict[str, Path] = {inventory_module.rel_path(base, item): item for item in all_projects}
        for item in excluded_paths:
            discovered.setdefault(inventory_module.rel_path(base, item), item)
        for project in projects:
            classification = index.get(inventory_module.rel_path(base, project_roots[project["project_id"]]))
            project["classification"] = classification or {"lifecycle": "UNKNOWN", "group": "UNKNOWN", "source": joined["source"]}
        for item in excluded:
            classification = index.get(inventory_module.rel_path(base, Path(item["path"])))
            if classification:
                item["classification"] = classification
        # A checkout nested under an excluded project leaves discovery scope along
        # with its parent; that's the exclusion propagating, not a missing checkout.
        excluded_keys = {inventory_module.rel_path(base, item) for item in excluded_paths}
        declared_not_discovered = sorted(key for key in index if key not in discovered and not any(key == ex or key.startswith(ex + "/") for ex in excluded_keys))
        discovered_not_declared = sorted(key for key in discovered if key not in index)
        applied: dict[str, int] = {}
        for key, entry in index.items():
            if key in discovered:
                applied[entry["lifecycle"]] = applied.get(entry["lifecycle"], 0) + 1
        inventory_block = {
            "schema": inventory.get("schema"),
            "source": joined["source"],
            "observed_at": inventory.get("observed_at"),
            "revised": inventory.get("revised"),
            "declared": {"rows_in_scope": len(index), "rows_out_of_scope": len(joined["out_of_scope"])},
            "applied": dict(sorted(applied.items())),
            "drift": {"declared_not_discovered": declared_not_discovered, "discovered_not_declared": discovered_not_declared},
        }
        if declared_not_discovered:
            inventory_findings.append({"finding_id": "INV-001", "severity": "medium", "category": "inventory", "status": "missing", "subject": "portfolio.inventory", "message": "The portfolio inventory declares checkouts that discovery did not find.", "evidence": declared_not_discovered})
        if discovered_not_declared:
            inventory_findings.append({"finding_id": "INV-002", "severity": "medium", "category": "inventory", "status": "unclassified", "subject": "portfolio.inventory", "message": "Discovery found checkouts the portfolio inventory does not classify.", "evidence": discovered_not_declared})
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
    if inventory_block is not None:
        snapshot["inventory"] = inventory_block
    snapshot["findings"] = findings(projects, artifacts, dependencies, excluded, snapshot["source_of_truth"], raw_dependencies, alias_conflicts, published_ids, legacy_relationship_manifests) + inventory_findings
    snapshot["snapshot_id"] = snapshot_hash(snapshot)
    return portable_snapshot(snapshot)
