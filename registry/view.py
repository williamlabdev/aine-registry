"""Portable static and loopback-only portfolio views."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

try:
    from .analysis import snapshot_validation_errors
    from .common import no_absolute_paths, print_json
    from .evidence import list_store_records
except ImportError:  # direct execution: python3 registry/aine_registry.py
    from analysis import snapshot_validation_errors
    from common import no_absolute_paths, print_json
    from evidence import list_store_records

def portfolio_html(snapshot: dict[str, Any]) -> str:
    portfolio = snapshot.get("portfolio", {})
    projects = snapshot.get("projects", [])
    artifacts = snapshot.get("artifacts", [])
    dependencies = snapshot.get("dependencies", [])
    findings_data = snapshot.get("findings", [])
    project_rows = "\n".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(project.get("project_id", "UNKNOWN"))),
            html.escape(str(project.get("kind", "UNKNOWN"))),
            html.escape(str(project.get("owner", "UNKNOWN"))),
            html.escape(str(project.get("ownership", {}).get("team", "UNKNOWN"))),
            html.escape(str(project.get("path", "UNKNOWN"))),
        )
        for project in projects
    ) or '<tr><td colspan="5">No projects</td></tr>'
    title = html.escape(str(portfolio.get("name", "AINE Portfolio")))
    snapshot_id = html.escape(str(snapshot.get("snapshot_id", "UNKNOWN")))
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172033;background:#f7f8fb}}header,section{{background:#fff;border:1px solid #dfe3ec;border-radius:12px;padding:1.25rem;margin-bottom:1rem}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #e5e7eb;text-align:left;padding:.6rem}}.metrics{{display:flex;gap:1rem;flex-wrap:wrap}}.metric{{background:#eef2ff;border-radius:8px;padding:.75rem 1rem}}</style></head>
<body><header><h1>{title}</h1><p>Read-only AINE portfolio view</p><p><strong>Snapshot:</strong> <code>{snapshot_id}</code></p></header>
<section><div class="metrics"><div class="metric"><strong>{projects_count}</strong><br>Projects</div><div class="metric"><strong>{artifacts_count}</strong><br>Artifacts</div><div class="metric"><strong>{dependencies_count}</strong><br>Dependencies</div><div class="metric"><strong>{findings_count}</strong><br>Findings</div></div></section>
<section><h2>Projects</h2><table><thead><tr><th>Project</th><th>Kind</th><th>Owner</th><th>Team</th><th>Portable path</th></tr></thead><tbody>{project_rows}</tbody></table></section></body></html>
""".format(title=title, snapshot_id=snapshot_id, projects_count=len(projects), artifacts_count=len(artifacts), dependencies_count=len(dependencies), findings_count=len(findings_data), project_rows=project_rows)


def command_view(args: argparse.Namespace) -> int:
    try:
        snapshot = json.loads(Path(args.snapshot).expanduser().read_text(encoding="utf-8"))
        errors = snapshot_validation_errors(snapshot)
        if errors:
            raise ValueError("invalid snapshot: " + "; ".join(errors))
        if not no_absolute_paths(snapshot):
            raise ValueError("snapshot contains an absolute local path")
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(portfolio_html(snapshot), encoding="utf-8")
        print_json({"status": "written", "format": "html", "projects": len(snapshot.get("projects", [])), "output": output.name})
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"could not render portfolio view: {exc}", file=sys.stderr); return 2


def command_serve(args: argparse.Namespace) -> int:
    try:
        snapshot = json.loads(Path(args.snapshot).expanduser().read_text(encoding="utf-8"))
        errors = snapshot_validation_errors(snapshot)
        if errors:
            raise ValueError("invalid snapshot: " + "; ".join(errors))
        if not no_absolute_paths(snapshot):
            raise ValueError("snapshot contains an absolute local path")
        store_root = Path(args.store).expanduser().resolve() if args.store else None
        records = lambda correlation=None: list_store_records(store_root, correlation) if store_root else []
        document = portfolio_html(snapshot).encode("utf-8")
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                route = urlsplit(self.path)
                if route.path == "/":
                    content_type, payload = "text/html; charset=utf-8", document
                elif route.path == "/api/snapshot":
                    content_type, payload = "application/json; charset=utf-8", snapshot_json
                elif route.path == "/api/evidence":
                    # The read-only API answers the same correlation question
                    # the CLI does, so a reviewer is not forced to choose a
                    # surface to get a run's records together.
                    correlation = parse_qs(route.query).get("correlation", [None])[0]
                    content_type, payload = "application/json; charset=utf-8", json.dumps(records(correlation), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
                else:
                    self.send_error(404, "not found")
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *values: Any) -> None:
                return

        if args.check:
            print_json({"status": "ready", "routes": ["/", "/api/snapshot", "/api/evidence"], "host": args.host, "port": args.port, "records": len(records())})
            return 0
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        print_json({"status": "serving", "host": args.host, "port": server.server_port, "routes": ["/", "/api/snapshot", "/api/evidence"]})
        server.serve_forever()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"could not serve portfolio: {exc}", file=sys.stderr); return 2
