"""Snapshot analysis, impact, policy, and preflight primitives."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from .constants import *
    from .common import *
except ImportError:  # direct execution: python3 registry/aine_registry.py
    from constants import *
    from common import *

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
    return [p for p in snapshot["projects"] if value in {p["project_id"], p["name"], p["path"], p["checkout_id"], p["repository_id"], p.get("declared_id")} or value in p["project_id"]]


def impact(snapshot: dict[str, Any], seed: str) -> dict[str, Any]:
    matched = project_matches(snapshot, seed)
    matched_artifacts = [a for a in snapshot["artifacts"] if seed in a["artifact_id"] or seed in a.get("workspace_path", "") or seed == a.get("path")]
    seed_ids = {p["project_id"] for p in matched} | {a["project_id"] for a in matched_artifacts}
    direct: list[dict[str, Any]] = []; transitive: list[dict[str, Any]] = []; visited = set(seed_ids); frontier = set(seed_ids); depth = 0
    while frontier:
        next_frontier: set[str] = set()
        for edge in snapshot["dependencies"]:
            # Portfolio/governance/integration relationships are useful for
            # topology queries, but they do not imply that a source change
            # requires validation of every related project.  A manifest may
            # opt a relationship into impact propagation with `impact: true`.
            if edge.get("kind") in {"portfolio", "governance", "integration"} and edge.get("impact") is not True:
                continue
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
