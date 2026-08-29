"""CLI compatibility layer for the AINE Registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

try:
    from .version import VERSION
    from .constants import *
    from .common import *
    from .discovery import discover
    from .analysis import *
    from .evidence import command_evidence, evidence_record
    from .view import command_serve, command_view
except ImportError:  # direct execution: python3 registry/aine_registry.py
    from version import VERSION
    from constants import *
    from common import *
    from discovery import discover
    from analysis import *
    from evidence import command_evidence, evidence_record
    from view import command_serve, command_view

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
def load_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    if not args.snapshot: return discover(configured_roots(args), relationship_overlays=configured_overlays(args), published_projects=configured_published_projects(args), inventory=configured_inventory(args))
    try: return portable_snapshot(json.loads(Path(args.snapshot).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc: raise SystemExit(f"could not read snapshot: {exc}")

def add_workspace_options(command_parser: argparse.ArgumentParser) -> None:
    """Allow the ergonomic `aine-registry command --root ...` form.

    Global options remain supported for backwards compatibility. Suppressed
    defaults prevent subcommand options from overwriting global values.
    """
    command_parser.add_argument("--workspace", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    command_parser.add_argument("--root", dest="roots", action="append", default=argparse.SUPPRESS, help="workspace root; repeat for multi-root discovery")
    command_parser.add_argument("--snapshot", default=argparse.SUPPRESS, help="read an existing JSON snapshot")
    command_parser.add_argument("--exclude-project", action="append", default=argparse.SUPPRESS, help="exclude project name; repeat as needed")
    command_parser.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    command_parser.add_argument("--inventory", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aine-registry",
        description="Read-only AINE multi-root portfolio registry",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    p.add_argument("--workspace", help="legacy single workspace root")
    p.add_argument("--root", dest="roots", action="append", help="workspace root; repeat for multi-root discovery")
    p.add_argument("--snapshot", help="read an existing JSON snapshot")
    p.add_argument("--exclude-project", action="append", help="exclude project name; repeat as needed")
    p.add_argument("--config", help="local-only config path; never included in portable snapshots")
    p.add_argument("--inventory", help="portfolio inventory file to join lifecycle classification from")
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
    evidence_list.add_argument("--correlation", help="list only records sharing this correlation identifier")
    evidence_get = evidence_sub.add_parser("get", help="read and verify a stored record")
    evidence_get.add_argument("--id", required=True, help="record ID or digest")
    evidence_get.add_argument("--store", required=True, help="local evidence store directory")
    evidence_export = evidence_sub.add_parser("export", help="export a portable audit bundle")
    evidence_export.add_argument("--store", required=True, help="local evidence store directory")
    evidence_export.add_argument("--correlation", help="bundle only records sharing this correlation identifier")
    evidence_export.add_argument("--output", help="write the bundle")
    evidence_retention = evidence_sub.add_parser("retention", help="produce a read-only retention manifest")
    evidence_retention.add_argument("--store", required=True, help="local evidence store directory")
    evidence_retention.add_argument("--retain-days", type=int, required=True, help="retention window for review")
    evidence_retention.add_argument("--as-of", required=True, help="ISO-8601 evaluation time")
    evidence_retention.add_argument("--output", help="write the manifest")
    view = sub.add_parser("view", help="render a portable snapshot as static HTML")
    view.add_argument("--snapshot", required=True, help="portable registry snapshot JSON")
    view.add_argument("--output", required=True, help="HTML output path")
    serve = sub.add_parser("serve", help="serve a portable snapshot with the stdlib HTTP server")
    serve.add_argument("--snapshot", required=True, help="portable registry snapshot JSON")
    serve.add_argument("--store", help="optional local evidence store directory")
    serve.add_argument("--host", default="127.0.0.1", help="bind host; default is loopback")
    serve.add_argument("--port", type=int, default=8080, help="bind port")
    serve.add_argument("--check", action="store_true", help="validate the server configuration without starting it")
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


def command(args: argparse.Namespace) -> int:
    if args.action == "init":
        return write_local_config(args)
    if args.action == "portfolio" and args.portfolio_action == "list":
        snapshot = load_snapshot(args); print_json(snapshot["portfolio"]); return 0
    if args.action == "evidence":
        return command_evidence(args)
    if args.action == "view":
        return command_view(args)
    if args.action == "serve":
        return command_serve(args)
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
    snapshot = load_snapshot(args) if args.snapshot else discover(roots, set(args.exclude_project or DEFAULT_EXCLUDED_PROJECTS), configured_overlays(args), configured_published_projects(args), configured_inventory(args))
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
        return 0 if not errors else 1
    elif action == "handoff":
        print_json({"portfolio_id": snapshot["portfolio"]["portfolio_id"], "snapshot_id": snapshot["snapshot_id"], "projects": [{"project_id": p["project_id"], "root_id": p["root_id"], "checkout_id": p["checkout_id"], "path": p["path"]} for p in snapshot["projects"]], "cross_root_dependencies": sum(e["scope"] == "cross_root" for e in snapshot["dependencies"])})
    else: return 2
    return 0
