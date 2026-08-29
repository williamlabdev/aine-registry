from __future__ import annotations

import json
import inspect
import os
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
import sys
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).parent))
import aine_registry as registry
import inventory


def make_git_project(path: Path, remote: str, files: dict[str, str] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    for name, content in (files or {}).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


class MultiRootRegistryTests(unittest.TestCase):
    def test_public_facade_reexports_split_implementation_modules(self):
        expected_modules = {
            "discover": "discovery.py",
            "preflight": "analysis.py",
            "store_record": "evidence.py",
            "portfolio_html": "view.py",
            "main": "cli.py",
        }
        for name, expected_module in expected_modules.items():
            source = inspect.getsourcefile(getattr(registry, name))
            self.assertIsNotNone(source)
            self.assertTrue(source.endswith(expected_module), (name, source))

    def test_cli_reports_version(self):
        result = subprocess.run([
            sys.executable, str(Path(__file__).parent / "aine_registry.py"), "--version",
        ], capture_output=True, text=True, check=True)
        self.assertTrue(result.stdout.startswith("aine-registry "), result.stdout)
        self.assertIn(registry.VERSION, result.stdout)

    def test_cli_uses_canonical_program_name(self):
        help_text = registry.parser().format_help()
        self.assertIn("usage: aine-registry", help_text)

    def test_validate_cli_returns_nonzero_for_invalid_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "invalid.json"
            snapshot.write_text(json.dumps({"schema": "aine.registry.v1"}), encoding="utf-8")
            result = subprocess.run([
                sys.executable,
                str(Path(__file__).parent / "aine_registry.py"),
                "validate",
                "--snapshot",
                str(snapshot),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)["valid"])

    def test_multi_root_duplicate_checkout_and_cross_root_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            core = base / "core"
            side = base / "side-projects"
            make_git_project(core / "app", "https://example.test/shared.git", {"README.md": str((side / "lib").resolve()) + "\n"})
            make_git_project(side / "lib", "https://example.test/shared.git", {"generated.json": "{}\n"})

            snapshot = registry.discover([core, side], excluded_names=set())
            self.assertEqual(snapshot["schema"], "aine.registry.v1")
            self.assertEqual({r["root_id"] for r in snapshot["portfolio"]["workspace_roots"]}, {"core", "side-projects"})
            self.assertEqual(len(snapshot["projects"]), 2)
            self.assertEqual(len(snapshot["repositories"]), 1)
            self.assertEqual(len(snapshot["checkouts"]), 2)
            self.assertTrue(any(a["root_id"] == "side-projects" and a["role"] == "generated" for a in snapshot["artifacts"]))
            self.assertTrue(any(e["scope"] == "cross_root" for e in snapshot["dependencies"]))

    def test_same_named_workspace_roots_receive_unique_portable_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            first = base / "one" / "workspace"
            second = base / "two" / "workspace"
            make_git_project(first / "app", "https://example.test/first.git")
            make_git_project(second / "app", "https://example.test/second.git")
            snapshot = registry.discover([first, second], excluded_names=set())
            self.assertEqual({root["root_id"] for root in snapshot["portfolio"]["workspace_roots"]}, {"workspace", "workspace-2"})
            self.assertEqual({project["project_id"] for project in snapshot["projects"]}, {"workspace.app", "workspace-2.app"})
            self.assertEqual(registry.snapshot_validation_errors(snapshot), [])

    def test_deterministic_hash_ignores_observed_time_and_local_path(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "core"
            make_git_project(root / "app", "https://example.test/app.git")
            first = registry.discover([root], excluded_names=set())
            second = registry.discover([root], excluded_names=set())
            self.assertEqual(first["snapshot_id"], second["snapshot_id"])

    def test_portable_snapshot_redacts_local_paths_and_normalizes_references(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            core = base / "core"
            side = base / "side-projects"
            make_git_project(core / "app", "https://example.test/app.git", {"README.md": str(side / "lib") + "\n"})
            make_git_project(side / "lib", "https://example.test/lib.git")
            snapshot = registry.discover([core, side], excluded_names=set())
            serialized = json.dumps(snapshot, ensure_ascii=False)
            self.assertNotIn(str(base), serialized)
            self.assertNotIn(str(Path.home()), serialized)
            self.assertIn("side-projects:lib", serialized)
            self.assertTrue(registry.no_absolute_paths(snapshot))

    def test_relocation_keeps_portable_hash_equivalent(self):
        with tempfile.TemporaryDirectory() as first_temp, tempfile.TemporaryDirectory() as second_temp:
            first_base = Path(first_temp); second_base = Path(second_temp)
            for base in (first_base, second_base):
                make_git_project(base / "core" / "app", "https://example.test/app.git", {"generated.json": "{}\n"})
                make_git_project(base / "side-projects" / "tool", "https://example.test/tool.git")
            first = registry.discover([first_base / "core", first_base / "side-projects"], excluded_names=set())
            second = registry.discover([second_base / "core", second_base / "side-projects"], excluded_names=set())
            self.assertEqual(first["snapshot_id"], second["snapshot_id"])

    def test_cli_normal_output_is_path_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "core"
            make_git_project(root / "app", "https://example.test/app.git")
            result = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "--root", str(root), "discover"], capture_output=True, text=True, check=True)
            self.assertNotIn(str(root), result.stdout)
            self.assertNotIn(str(Path.home()), result.stdout)
            self.assertIn('"root_id": "core"', result.stdout)

    def test_validate_checks_snapshot_structure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "app", "https://example.test/app.git")
            snapshot = registry.discover([root], excluded_names=set())
            self.assertEqual(registry.snapshot_validation_errors(snapshot), [])
            broken = dict(snapshot)
            broken["projects"] = [{"name": "missing-id"}]
            self.assertTrue(any("projects[0]" in error for error in registry.snapshot_validation_errors(broken)))

    def test_module_import_adapter_supports_python_javascript_go_and_rust(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "app", "https://example.test/app.git", {
                "py/app.py": "from .client import Client\nimport requests\nimport requests as req\n",
                "py/client.py": "class Client: pass\n",
                "web/index.ts": "import { value } from './util';\nconst x = require('lodash');\nconst y = import('@scope/events');\nconst z = import(module_name);\n",
                "web/util.ts": "export const value = 1;\n",
                "go.mod": "module example.com/acme\n\ngo 1.22\n",
                "cmd/main.go": "package main\nimport (\n  \"fmt\"\n  \"example.com/acme/dep\"\n)\n",
                "dep/dep.go": "package dep\n",
                "src/main.rs": "mod parser;\nuse serde::Deserialize;\n",
                "src/parser.rs": "pub struct Parser;\n",
            })
            snapshot = registry.discover([root], excluded_names=set())
            imports = snapshot["imports"]
            self.assertEqual({item["language"] for item in imports}, {"python", "typescript", "go", "rust"})
            self.assertTrue(any(item["specifier"] == ".client" and item["resolution"] == "local" for item in imports))
            self.assertTrue(any(item["specifier"] == "./util" and item["resolution"] == "local" for item in imports))
            self.assertTrue(any(item["specifier"] == "requests" and item["resolution"] == "external" for item in imports))
            self.assertTrue(any(item["specifier"] == "requests" and item["target_project_id"] == "external:requests" for item in imports))
            self.assertTrue(any(item["specifier"] == "lodash" and item["resolution"] == "external" for item in imports))
            self.assertTrue(any(item["specifier"] == "lodash" and item["kind"] == "module_import" for item in imports))
            self.assertTrue(any(item["specifier"] == "@scope/events" and item["kind"] == "dynamic_import" for item in imports))
            self.assertTrue(any(item["specifier"] == "module_name" and item["resolution"] == "unresolved" for item in imports))
            self.assertTrue(any(item["specifier"] == "fmt" and item["language"] == "go" for item in imports))
            self.assertTrue(any(item["specifier"] == "example.com/acme/dep" and item["resolution"] == "local" for item in imports))
            self.assertTrue(any(item["specifier"] == "parser" and item["resolution"] == "local" for item in imports))
            self.assertTrue(any(item["specifier"] == "serde::Deserialize" and item["resolution"] == "external" for item in imports))
            self.assertEqual(registry.snapshot_validation_errors(snapshot), [])

    def test_python_script_mode_fallback_imports_resolve_to_siblings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "app", "https://example.test/app.git", {
                "pkg/cli.py": "try:\n    from .engine import run\nexcept ImportError:\n    from engine import run\n",
                "pkg/engine.py": "def run(): pass\n",
            })
            snapshot = registry.discover([root], excluded_names=set())
            resolutions = {(item["specifier"], item["resolution"]) for item in snapshot["imports"]}
            self.assertIn((".engine", "local"), resolutions)
            self.assertIn(("engine", "local"), resolutions)
            self.assertEqual([], [edge for edge in snapshot["dependencies"] if edge["target"]["project_id"] == "external:engine"])
            self.assertEqual(registry.snapshot_validation_errors(snapshot), [])

    def test_standard_library_imports_resolve_without_dependency_edges(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "app", "https://example.test/app.git", {
                "py/app.py": "import json\nfrom pathlib import Path\nimport requests\n",
                "web/index.ts": "import fs from 'node:fs';\nimport path from 'path';\nimport lodash from 'lodash';\n",
                "go.mod": "module example.com/acme\n\ngo 1.22\n",
                "cmd/main.go": "package main\nimport (\n  \"net/http\"\n  \"github.com/acme/sdk\"\n)\n",
                "src/main.rs": "use std::fmt;\nuse serde::Deserialize;\n",
            })
            snapshot = registry.discover([root], excluded_names=set())
            by_specifier = {item["specifier"]: item for item in snapshot["imports"]}
            expected = {
                "json": "stdlib:python:json",
                "pathlib": "stdlib:python:pathlib",
                "node:fs": "stdlib:typescript:fs",
                "path": "stdlib:typescript:path",
                "net/http": "stdlib:go:net/http",
                "std::fmt": "stdlib:rust:std",
            }
            for specifier, target in expected.items():
                self.assertEqual(by_specifier[specifier]["resolution"], "stdlib", specifier)
                self.assertEqual(by_specifier[specifier]["target_project_id"], target, specifier)
            for specifier in ("requests", "lodash", "github.com/acme/sdk", "serde::Deserialize"):
                self.assertEqual(by_specifier[specifier]["resolution"], "external", specifier)
            targets = {edge["target"]["project_id"] for edge in snapshot["dependencies"]}
            self.assertEqual([], [target for target in targets if target.startswith("stdlib:")])
            unknown = {finding["subject"] for finding in snapshot["findings"] if finding["finding_id"] == "DEP-001"}
            self.assertEqual([], [subject for subject in unknown if "stdlib" in subject])
            self.assertEqual(registry.snapshot_validation_errors(snapshot), [])

    def test_prose_beginning_with_import_is_not_recorded_as_an_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "app", "https://example.test/app.git", {
                "py/app.py": '"""Docstring.\n\nimport records keep the specifier for explainability.\n"""\nimport json\n',
            })
            snapshot = registry.discover([root], excluded_names=set())
            specifiers = {item["specifier"] for item in snapshot["imports"]}
            self.assertEqual({"json"}, specifiers)

    def test_scan_alias_accepts_root_after_subcommand(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "app", "https://example.test/app.git")
            result = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "scan", "--root", str(root)], capture_output=True, text=True, check=True)
            self.assertIn('"project_id": "workspace.app"', result.stdout)

    def test_impact_accepts_root_after_subcommand(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "app", "https://example.test/app.git")
            result = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "impact", "--root", str(root), "--project", "app"], capture_output=True, text=True, check=True)
            self.assertIn('"query": "app"', result.stdout)

    def test_manifest_metadata_and_preflight_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            provider = root / "provider"
            consumer = root / "consumer"
            make_git_project(provider, "https://example.test/provider.git", {"api.yaml": "openapi: 3.0.0\n"})
            make_git_project(consumer, "https://example.test/consumer.git")
            (provider / ".aine").mkdir()
            (provider / ".aine" / "registry.json").write_text(json.dumps({
                "artifacts": [{"id": "provider-api", "path": "api.yaml", "role": "source", "source_of_truth": True}],
                "source_of_truth": [
                    {"domain": "payments.api", "authority": {"project_id": "workspace.provider", "artifact": "provider-api"}},
                    {"domain": "payments.api", "authority": {"project_id": "workspace.consumer", "artifact": "consumer-api"}},
                ],
            }), encoding="utf-8")
            (consumer / ".aine").mkdir()
            (consumer / ".aine" / "registry.json").write_text(json.dumps({
                "dependencies": [
                    {"target": "workspace.provider", "kind": "runtime_api", "status": "active"},
                    {"target": "workspace.provider", "kind": "runtime_api", "status": "historical"},
                ],
                "relationships": [{"target": "workspace.provider", "relationship_type": "event_consumer", "status": "planned"}, {"target": "workspace.provider", "kind": "runtime_api", "status": "active"}],
            }), encoding="utf-8")
            snapshot = registry.discover([root], excluded_names=set())
            self.assertTrue(any(a["artifact_id"] == "provider-api" for a in snapshot["artifacts"]))
            self.assertTrue(any(e["target"]["project_id"] == "workspace.provider" and e["kind"] == "runtime_api" for e in snapshot["dependencies"]))
            self.assertTrue(any(e.get("relationship_type") == "event_consumer" and e["status"] == "planned" for e in snapshot["dependencies"]))
            self.assertEqual(len(snapshot["relationships"]), 2)
            result = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "relationships", "--root", str(root)], capture_output=True, text=True, check=True)
            self.assertIn('"relationship_type": "event_consumer"', result.stdout)
            filtered = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "relationships", "--root", str(root), "--relationship-status", "planned"], capture_output=True, text=True, check=True)
            self.assertIn('"relationship_type": "event_consumer"', filtered.stdout)
            empty = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "relationships", "--root", str(root), "--relationship-status", "active"], capture_output=True, text=True, check=True)
            self.assertEqual(len(json.loads(empty.stdout)), 1)
            context = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "context", "--root", str(root), "--project", "consumer"], capture_output=True, text=True, check=True)
            context_data = json.loads(context.stdout)
            self.assertEqual([item["name"] for item in context_data["projects"]], ["consumer"])
            self.assertEqual(len(context_data["relationships"]), 2)
            report = registry.preflight(snapshot, ["provider/api.yaml"], [root])
            self.assertEqual([a["artifact_id"] for a in report["matched_artifacts"]], ["provider-api"])
            self.assertIn("workspace.consumer", {p["project_id"] for p in report["affected_projects"]})
            self.assertTrue(report["read_only"])
            self.assertTrue(report["source_of_truth"])
            self.assertTrue(any(item["finding_id"] == "SOT-001" for item in snapshot["findings"]))
            self.assertTrue(any(item["finding_id"] == "REL-002" for item in snapshot["findings"]))

    def test_openapi_contract_adapter_registers_schema_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "service", "https://example.test/service.git", {
                "openapi.yaml": "openapi: 3.0.3\ninfo:\n  title: Service\n  version: 1.0.0\npaths: {}\n",
            })
            snapshot = registry.discover([root], excluded_names=set())
            contract = next(item for item in snapshot["artifacts"] if item["path"] == "openapi.yaml")
            self.assertEqual(contract["role"], "schema")
            self.assertEqual(contract["kind"], "openapi_contract")
            self.assertEqual(contract["contract"], {"format": "openapi", "version": "3.0.3"})
            report = registry.preflight(snapshot, ["service/openapi.yaml"], [root])
            self.assertEqual([item["artifact_id"] for item in report["matched_artifacts"]], [contract["artifact_id"]])

    def test_protobuf_contract_adapter_registers_schema_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "service", "https://example.test/service.git", {
                "proto/orders.proto": "syntax = \"proto3\";\npackage orders.v1;\nservice Orders { rpc GetOrder (GetOrderRequest) returns (Order); }\nmessage Order { string id = 1; }\n",
            })
            snapshot = registry.discover([root], excluded_names=set())
            contract = next(item for item in snapshot["artifacts"] if item["path"] == "proto/orders.proto")
            self.assertEqual(contract["role"], "schema")
            self.assertEqual(contract["kind"], "protobuf_contract")
            self.assertEqual(contract["contract"]["format"], "protobuf")
            self.assertEqual(contract["contract"]["syntax"], "proto3")
            self.assertEqual(contract["contract"]["package"], "orders.v1")
            self.assertEqual(contract["contract"]["services"], ["Orders"])

    def test_asyncapi_contract_adapter_registers_schema_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "events", "https://example.test/events.git", {
                "asyncapi.yaml": "asyncapi: 3.0.0\ninfo:\n  title: Events\n  version: 1.0.0\nchannels:\n  order.created:\n    messages: {}\n",
            })
            snapshot = registry.discover([root], excluded_names=set())
            contract = next(item for item in snapshot["artifacts"] if item["path"] == "asyncapi.yaml")
            self.assertEqual(contract["role"], "schema")
            self.assertEqual(contract["kind"], "asyncapi_contract")
            self.assertEqual(contract["contract"]["format"], "asyncapi")
            self.assertEqual(contract["contract"]["version"], "3.0.0")

    def test_deployment_adapter_registers_docker_and_kubernetes_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "service", "https://example.test/service.git", {
                "Dockerfile": "FROM python:3.12\nCMD [\"python\", \"-m\", \"service\"]\n",
                "deploy/service.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: service\n",
            })
            snapshot = registry.discover([root], excluded_names=set())
            docker = next(item for item in snapshot["artifacts"] if item["path"] == "Dockerfile")
            kubernetes = next(item for item in snapshot["artifacts"] if item["path"] == "deploy/service.yaml")
            self.assertEqual(docker["role"], "deployment")
            self.assertEqual(docker["kind"], "dockerfile")
            self.assertEqual(docker["deployment"]["format"], "docker")
            self.assertEqual(kubernetes["kind"], "kubernetes_manifest")
            report = registry.preflight(snapshot, ["service/deploy/service.yaml"], [root])
            self.assertEqual(report["matched_artifacts"][0]["artifact_id"], kubernetes["artifact_id"])

    def test_github_actions_adapter_registers_ci_provenance_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "service", "https://example.test/service.git", {
                ".github/workflows/ci.yml": "name: CI\non:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n  lint:\n    runs-on: ubuntu-latest\n",
            })
            snapshot = registry.discover([root], excluded_names=set())
            workflow = next(item for item in snapshot["artifacts"] if item["path"] == ".github/workflows/ci.yml")
            self.assertEqual(workflow["role"], "provenance")
            self.assertEqual(workflow["kind"], "github_actions_workflow")
            self.assertEqual(workflow["ci"]["provider"], "github_actions")
            self.assertEqual(workflow["ci"]["jobs"], ["lint", "test"])

    def test_preflight_cli_accepts_change_after_subcommand(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "app", "https://example.test/app.git", {"api.yaml": "openapi: 3.0.0\n"})
            result = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "preflight", "--root", str(root), "--change", "app/api.yaml"], capture_output=True, text=True, check=True)
            self.assertIn('"read_only": true', result.stdout)

    def test_preflight_matches_change_in_workspace_root_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "api.yaml").write_text("openapi: 3.0.0\n", encoding="utf-8")
            snapshot = registry.discover([root], excluded_names=set())
            report = registry.preflight(snapshot, ["api.yaml"], [root])
            self.assertTrue(report["matched_projects"])

    def test_git_diff_preflight_reports_risk_and_markdown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://example.test/service.git"], check=True)
            (root / "api.yaml").write_text("openapi: 3.0.0\n", encoding="utf-8")
            (root / ".aine").mkdir()
            (root / ".aine" / "registry.json").write_text(json.dumps({
                "project": {
                    "owner": "platform-team",
                    "policy": {
                        "mode": "advisory",
                        "require_approval_for": ["high"],
                        "deny_unknown_changes": True,
                        "required_checks": ["test"],
                    },
                },
                "artifacts": [{"id": "service-api", "path": "api.yaml", "role": "source", "risk": "high", "approval_required": True}],
            }), encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
            (root / "api.yaml").write_text("openapi: 3.0.1\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "preflight", "--root", str(root), "--diff", "--format", "markdown"], capture_output=True, text=True, check=True)
            self.assertIn("Risk: **high**", result.stdout)
            self.assertIn("platform-team", result.stdout)
            self.assertIn("api.yaml", result.stdout)

    def test_preflight_evidence_output_and_handoff(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root, "https://example.test/service.git", {"api.yaml": "openapi: 3.0.0\n"})
            (root / ".aine").mkdir()
            (root / ".aine" / "registry.json").write_text(json.dumps({
                "project": {
                    "owner": "platform-team",
                    "policy": {
                        "require_approval_for": ["high"],
                        "deny_unknown_changes": True,
                        "required_checks": ["test"],
                    },
                },
                "artifacts": [{"id": "service-api", "path": "api.yaml", "role": "source", "risk": "high", "approval_required": True}],
            }), encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
            (root / "api.yaml").write_text("openapi: 3.0.1\n", encoding="utf-8")
            evidence = Path(temp) / "preflight.json"
            result = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "preflight", "--root", str(root), "--diff", "--output", str(evidence)], capture_output=True, text=True, check=True)
            self.assertIn('"status": "written"', result.stdout)
            report = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(report["evidence"]["schema"], "aine.evidence.v1")
            self.assertEqual(report["schema"], "aine.evidence.v1")
            self.assertEqual(report["evidence_id"], report["evidence"]["evidence_id"])
            self.assertEqual(report["policy"]["mode"], "advisory")
            self.assertEqual(report["policy"]["status"], "fail")
            self.assertFalse(report["policy"]["enforced_failure"])
            self.assertEqual(report["policy"]["exit_code"], 0)
            self.assertEqual(report["evidence"]["claims"]["policy_mode"], "advisory")
            self.assertFalse(report["evidence"]["claims"]["policy_enforced_failure"])
            self.assertTrue(any(check["rule"] == "required_checks" for check in report["policy"]["checks"]))
            handoff = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "handoff", "--preflight", str(evidence)], capture_output=True, text=True, check=True)
            handoff_data = json.loads(handoff.stdout)
            self.assertEqual(handoff_data["schema"], "aine.handoff.v1")
            self.assertEqual(handoff_data["status"], "human_review_required")
            self.assertEqual(handoff_data["approval"]["schema"], "aine.approval.v1")
            self.assertEqual(handoff_data["approval"]["status"], "blocked")

            handoff_file = Path(temp) / "handoff.json"
            handoff_file.write_text(json.dumps(handoff_data), encoding="utf-8")
            approval = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "approval", "--handoff", str(handoff_file)], capture_output=True, text=True, check=True)
            approval_data = json.loads(approval.stdout)
            self.assertEqual(approval_data["schema"], "aine.approval.v1")
            self.assertEqual(approval_data["approval_id"], handoff_data["approval"]["approval_id"])
            decided = subprocess.run([
                sys.executable, str(Path(__file__).parent / "aine_registry.py"), "approval",
                "--handoff", str(handoff_file), "--decision", "approved", "--decided-by", "human.william",
            ], capture_output=True, text=True, check=True)
            decided_data = json.loads(decided.stdout)
            self.assertEqual(decided_data["status"], "approved")
            self.assertEqual(decided_data["decision_source"], "external_input")
            self.assertEqual(decided_data["decided_by"], "human.william")

            enforced_evidence = Path(temp) / "enforced-preflight.json"
            enforced = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "preflight", "--root", str(root), "--diff", "--policy-mode", "enforced", "--output", str(enforced_evidence)], capture_output=True, text=True)
            self.assertEqual(enforced.returncode, 1)
            enforced_report = json.loads(enforced_evidence.read_text(encoding="utf-8"))
            self.assertEqual(enforced_report["policy"]["mode"], "enforced")
            self.assertTrue(enforced_report["policy"]["enforced_failure"])
            self.assertEqual(enforced_report["policy"]["exit_code"], 1)
            self.assertIn("required_checks", [check["rule"] for check in enforced_report["policy"]["checks"]])
            enforced_handoff = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "handoff", "--preflight", str(enforced_evidence)], capture_output=True, text=True, check=True)
            enforced_handoff_data = json.loads(enforced_handoff.stdout)
            self.assertEqual(enforced_handoff_data["status"], "human_review_required")
            self.assertEqual(enforced_handoff_data["policy"]["mode"], "enforced")

    def test_local_evidence_store_round_trip_and_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            record = {"schema": "aine.evidence.v1", "evidence_id": "evidence.store", "kind": "preflight", "snapshot_id": "snapshot.store", "claims": {"policy_status": "pass"}}
            input_path = base / "evidence.json"
            store = base / "evidence-store"
            input_path.write_text(json.dumps(record), encoding="utf-8")
            stored = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "evidence", "store", "--input", str(input_path), "--store", str(store)], capture_output=True, text=True, check=True)
            stored_data = json.loads(stored.stdout)
            self.assertEqual(stored_data["status"], "stored")
            listed = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "evidence", "list", "--store", str(store)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(listed.stdout)[0]["record_id"], stored_data["record_id"])
            fetched = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "evidence", "get", "--id", stored_data["record_id"], "--store", str(store)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(fetched.stdout), record)
            exported = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "evidence", "export", "--store", str(store)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(exported.stdout)["schema"], "aine.audit.bundle.v1")
            retention = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "evidence", "retention", "--store", str(store), "--retain-days", "30", "--as-of", "2026-08-19T00:00:00+00:00"], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(retention.stdout)["schema"], "aine.retention.manifest.v1")
            stored_path = store / f"{stored_data['record_id'].removeprefix('sha256:')}.json"
            tampered = json.loads(stored_path.read_text(encoding="utf-8"))
            tampered["record"]["claims"]["policy_status"] = "fail"
            stored_path.write_text(json.dumps(tampered), encoding="utf-8")
            invalid = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "evidence", "list", "--store", str(store)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(invalid.stdout)[0]["status"], "invalid")

    def test_integration_observation_joins_a_producer_run_to_a_stored_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            store = base / "evidence-store"
            script = str(Path(__file__).parent / "aine_registry.py")
            snapshot = {"schema": "aine.registry.v1", "projects": [], "dependencies": [], "read_only": True}
            snapshot_path = base / "snapshot.json"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            stored_snapshot = subprocess.run([sys.executable, script, "evidence", "store", "--input", str(snapshot_path), "--store", str(store)], capture_output=True, text=True, check=True)
            snapshot_id = json.loads(stored_snapshot.stdout)["record_id"]
            observation = {
                "schema": "aine.control-plane.integration-observation.v1",
                "evidence_id": "integration.producer.0123456789abcdef",
                "correlation_id": "corr.example.001",
                "producer": "producer",
                "project_id": "example.producer",
                "run_id": "producer-run-001",
                "snapshot_id": snapshot_id,
                "native_schema": "producer-evidence-v1",
                "native_digest": "sha256:" + "b" * 64,
                "status": "success",
                "claims": {"completed": True},
                "evidence_refs": ["producer://evidence/producer-run-001"],
                "read_only": True,
            }
            observation_path = base / "observation.json"
            observation_path.write_text(json.dumps(observation), encoding="utf-8")
            stored = subprocess.run([sys.executable, script, "evidence", "store", "--input", str(observation_path), "--store", str(store)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(stored.stdout)["status"], "stored")
            listed = json.loads(subprocess.run([sys.executable, script, "evidence", "list", "--store", str(store)], capture_output=True, text=True, check=True).stdout)
            correlations = {entry.get("correlation_id") for entry in listed}
            self.assertEqual(correlations, {None, "corr.example.001"})
            fetched = json.loads(subprocess.run([sys.executable, script, "evidence", "get", "--id", json.loads(stored.stdout)["record_id"], "--store", str(store)], capture_output=True, text=True, check=True).stdout)
            self.assertEqual(fetched["snapshot_id"], snapshot_id)
            self.assertNotIn("native", fetched)
            bundle = json.loads(subprocess.run([sys.executable, script, "evidence", "export", "--store", str(store)], capture_output=True, text=True, check=True).stdout)
            self.assertEqual({record["schema"] for record in bundle["records"]}, {"aine.registry.v1", "aine.control-plane.integration-observation.v1"})
            unsupported_path = base / "unsupported.json"
            unsupported_path.write_text(json.dumps({"schema": "producer-evidence-v1", "run_id": "producer-run-001"}), encoding="utf-8")
            rejected = subprocess.run([sys.executable, script, "evidence", "store", "--input", str(unsupported_path), "--store", str(store)], capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("unsupported record schema", rejected.stderr)

    def test_correlation_scoped_listing_and_bundle_name_what_they_leave_out(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            store = base / "evidence-store"
            script = str(Path(__file__).parent / "aine_registry.py")

            def run(*arguments):
                return json.loads(subprocess.run([sys.executable, script, *arguments], capture_output=True, text=True, check=True).stdout)

            def write(name, payload):
                path = base / name
                path.write_text(json.dumps(payload), encoding="utf-8")
                return str(path)

            # The snapshot carries a `snapshot_id` of its own, which is the
            # digest of its canonical content and not a stored record_id.
            snapshot = {"schema": "aine.registry.v1", "snapshot_id": "sha256:" + "c" * 64, "projects": [], "dependencies": [], "read_only": True}
            snapshot_id = run("evidence", "store", "--input", write("snapshot.json", snapshot), "--store", str(store))["record_id"]

            def observation(run_id, correlation_id, snapshot_reference):
                return {
                    "schema": "aine.control-plane.integration-observation.v1",
                    "evidence_id": f"integration.{run_id}",
                    "correlation_id": correlation_id,
                    "producer": "producer",
                    "project_id": "example.producer",
                    "run_id": run_id,
                    "snapshot_id": snapshot_reference,
                    "native_schema": "producer-evidence-v1",
                    "native_digest": "sha256:" + "b" * 64,
                    "status": "success",
                    "claims": {"completed": True},
                    "read_only": True,
                }

            first = run("evidence", "store", "--input", write("first.json", observation("run-001", "corr.one", snapshot_id)), "--store", str(store))["record_id"]
            second = run("evidence", "store", "--input", write("second.json", observation("run-002", "corr.one", snapshot_id)), "--store", str(store))["record_id"]
            run("evidence", "store", "--input", write("other.json", observation("run-003", "corr.two", snapshot_id)), "--store", str(store))

            scoped = run("evidence", "list", "--store", str(store), "--correlation", "corr.one")
            self.assertEqual({entry["record_id"] for entry in scoped}, {first, second})
            self.assertEqual(len(run("evidence", "list", "--store", str(store))), 4)
            self.assertEqual(run("evidence", "list", "--store", str(store), "--correlation", "corr.absent"), [])

            bundle = run("evidence", "export", "--store", str(store), "--correlation", "corr.one")
            self.assertEqual(bundle["correlation_id"], "corr.one")
            self.assertEqual({record["run_id"] for record in bundle["records"]}, {"run-001", "run-002"})
            # The snapshot belongs to no single correlation, so a scoped bundle
            # says it is absent instead of quietly widening its own scope.
            self.assertEqual(
                bundle["unresolved_refs"],
                [{"record_id": snapshot_id, "referenced_by": sorted([first, second]), "status": "out_of_scope"}],
            )

            whole = run("evidence", "export", "--store", str(store))
            self.assertNotIn("correlation_id", whole)
            # The snapshot's own snapshot_id is not a store reference and must
            # not be reported as a dangling one.
            self.assertEqual(whole["unresolved_refs"], [])
            self.assertEqual(len(whole["records"]), 4)

    def test_a_bundle_reports_a_snapshot_reference_the_store_cannot_produce(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            store = base / "evidence-store"
            script = str(Path(__file__).parent / "aine_registry.py")
            observation = {
                "schema": "aine.control-plane.integration-observation.v1",
                "evidence_id": "integration.orphan",
                "correlation_id": "corr.orphan",
                "producer": "producer",
                "project_id": "example.producer",
                "run_id": "run-orphan",
                "snapshot_id": "sha256:" + "d" * 64,
                "native_schema": "producer-evidence-v1",
                "native_digest": "sha256:" + "b" * 64,
                "status": "success",
                "claims": {},
                "read_only": True,
            }
            path = base / "orphan.json"
            path.write_text(json.dumps(observation), encoding="utf-8")
            stored = json.loads(subprocess.run([sys.executable, script, "evidence", "store", "--input", str(path), "--store", str(store)], capture_output=True, text=True, check=True).stdout)
            bundle = json.loads(subprocess.run([sys.executable, script, "evidence", "export", "--store", str(store)], capture_output=True, text=True, check=True).stdout)
            self.assertEqual(
                bundle["unresolved_refs"],
                [{"record_id": "sha256:" + "d" * 64, "referenced_by": [stored["record_id"]], "status": "missing"}],
            )

    def test_an_unreadable_record_stays_visible_in_a_correlation_scoped_listing(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            store = base / "evidence-store"
            script = str(Path(__file__).parent / "aine_registry.py")
            record = {"schema": "aine.registry.v1", "correlation_id": "corr.one", "projects": [], "read_only": True}
            path = base / "record.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            stored = json.loads(subprocess.run([sys.executable, script, "evidence", "store", "--input", str(path), "--store", str(store)], capture_output=True, text=True, check=True).stdout)
            stored_path = store / f"{stored['record_id'].removeprefix('sha256:')}.json"
            tampered = json.loads(stored_path.read_text(encoding="utf-8"))
            tampered["record"]["correlation_id"] = "corr.two"
            stored_path.write_text(json.dumps(tampered), encoding="utf-8")
            listed = json.loads(subprocess.run([sys.executable, script, "evidence", "list", "--store", str(store), "--correlation", "corr.one"], capture_output=True, text=True, check=True).stdout)
            self.assertEqual([entry["status"] for entry in listed], ["invalid"])

    def test_the_read_only_api_answers_the_same_correlation_question(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            store = base / "evidence-store"
            script = str(Path(__file__).parent / "aine_registry.py")
            snapshot = {"schema": "aine.registry.v1", "snapshot_id": "sha256:" + "c" * 64, "portfolio": {"portfolio_id": "example", "name": "example"}, "projects": [], "repositories": [], "checkouts": [], "artifacts": [], "dependencies": [], "source_of_truth": [], "findings": [], "read_only": True}
            snapshot_path = base / "snapshot.json"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            record = {"schema": "aine.registry.v1", "correlation_id": "corr.one", "projects": [], "read_only": True}
            record_path = base / "record.json"
            record_path.write_text(json.dumps(record), encoding="utf-8")
            stored = json.loads(subprocess.run([sys.executable, script, "evidence", "store", "--input", str(record_path), "--store", str(store)], capture_output=True, text=True, check=True).stdout)

            def fetch(port, query=""):
                with urlopen(f"http://127.0.0.1:{port}/api/evidence{query}", timeout=10) as response:
                    return json.loads(response.read().decode("utf-8"))

            # `-u` keeps the serving announcement out of a pipe buffer; it is
            # printed as indented JSON, so read until the closing brace.
            command = [sys.executable, "-u", script, "serve", "--snapshot", str(snapshot_path), "--store", str(store), "--host", "127.0.0.1", "--port", "0"]
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as server:
                try:
                    lines = []
                    while not lines or lines[-1].strip() != "}":
                        lines.append(server.stdout.readline())
                        self.assertNotEqual(lines[-1], "", "server exited before announcing a port")
                    announcement = json.loads("".join(lines))
                    self.assertEqual(announcement["status"], "serving")
                    port = announcement["port"]
                    self.assertEqual([entry["record_id"] for entry in fetch(port, "?correlation=corr.one")], [stored["record_id"]])
                    self.assertEqual(fetch(port, "?correlation=corr.absent"), [])
                    self.assertEqual(len(fetch(port)), 1)
                finally:
                    server.terminate()
                    server.wait(timeout=10)

    def test_static_portfolio_view_uses_portable_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "workspace"
            make_git_project(root / "service", "https://example.test/service.git")
            snapshot = registry.discover([root], excluded_names=set())
            snapshot_path = base / "snapshot.json"
            output = base / "portfolio.html"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            result = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "view", "--snapshot", str(snapshot_path), "--output", str(output)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)["format"], "html")
            rendered = output.read_text(encoding="utf-8")
            self.assertIn("workspace.service", rendered)
            self.assertNotIn(str(base), rendered)
            served = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "serve", "--snapshot", str(snapshot_path), "--port", "0", "--check"], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(served.stdout)["status"], "ready")

    def test_public_polyrepo_example_discovers_three_projects_and_cross_root_edge(self):
        source = Path(__file__).parent.parent / "examples" / "polyrepo"
        with tempfile.TemporaryDirectory() as temp:
            example = Path(temp) / "polyrepo"
            shutil.copytree(source, example)
            subprocess.run([sys.executable, str(example / "setup.py")], cwd=example, check=True, capture_output=True, text=True)
            result = subprocess.run([
                sys.executable, str(Path(__file__).parent / "aine_registry.py"), "discover",
                "--root", str(example / "core"), "--root", str(example / "side-projects"),
            ], capture_output=True, text=True, check=True)
            snapshot = json.loads(result.stdout)
            self.assertEqual({project["project_id"] for project in snapshot["projects"]}, {"core.checkout-service", "core.web-app", "side-projects.content-tool"})
            self.assertTrue(any(edge["scope"] == "cross_root" for edge in snapshot["dependencies"]))
            self.assertTrue(any(rule["domain"] == "checkout.api" for rule in snapshot["source_of_truth"]))

    def test_remote_credentials_are_not_exported(self):
        self.assertEqual(registry.normalized_remote("https://token:secret@example.test/org/repo.git"), "https://example.test/org/repo")

    def test_manifest_enforced_policy_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root, "https://example.test/service.git", {"api.yaml": "openapi: 3.0.0\n"})
            (root / ".aine").mkdir()
            (root / ".aine" / "registry.json").write_text(json.dumps({
                "project": {"policy": {"mode": "enforced", "required_checks": ["test"]}},
            }), encoding="utf-8")
            snapshot = registry.discover([root], excluded_names=set())
            report = registry.preflight(snapshot, ["api.yaml"], [root])
            self.assertEqual(report["policy"]["mode"], "enforced")
            self.assertTrue(report["policy"]["enforced_failure"])
            self.assertEqual(report["policy"]["exit_code"], 1)

    def test_manifest_declares_validation_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "service", "https://example.test/service.git", {"README.md": "service\n"})
            (root / "service" / ".aine").mkdir()
            (root / "service" / ".aine" / "registry.json").write_text(json.dumps({
                "project": {
                    "commands": {
                        "test": {"command": "python3 -m pytest -q", "evidence": "pyproject.toml"},
                        "verify": "make verify",
                    }
                }
            }), encoding="utf-8")
            snapshot = registry.discover([root], excluded_names=set())
            project = next(item for item in snapshot["projects"] if item["project_id"] == "workspace.service")
            self.assertEqual(project["commands"]["test"]["command"], "python3 -m pytest -q")
            self.assertEqual(project["commands"]["test"]["evidence"], "pyproject.toml")
            self.assertEqual(project["commands"]["verify"]["command"], "make verify")
            report = registry.preflight(snapshot, ["service"], [root])
            checks = {item["check"]: item["command"] for item in report["required_validation"]}
            self.assertEqual(checks["test"], "python3 -m pytest -q")
            self.assertEqual(checks["verify"], "make verify")

    def test_authorization_context_supports_rbac_and_abac_conditions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root, "https://example.test/service.git", {"README.md": "service\n"})
            (root / ".aine").mkdir()
            (root / ".aine" / "registry.json").write_text(json.dumps({
                "project": {
                    "ownership": {"team": "platform", "owners": ["team:platform"], "delegates": ["platform"]},
                    "policy": {
                        "authorization": {
                            "rules": [{
                                "id": "platform-preflight",
                                "effect": "allow",
                                "actions": ["preflight"],
                                "roles": ["developer"],
                                "conditions": {"subject.attributes.team": "platform", "resource.risk": "low"},
                            }, {
                                "id": "owned-preflight",
                                "effect": "allow",
                                "actions": ["preflight"],
                                "requires_ownership": True,
                            }]
                        }
                    }
                }
            }), encoding="utf-8")
            snapshot = registry.discover([root], excluded_names=set())
            report = registry.preflight(snapshot, ["README.md"], [root], authorization_context={
                "subject": {"id": "agent.codex", "roles": ["developer"], "attributes": {"team": "platform"}},
                "action": "preflight",
            })
            self.assertEqual(report["policy"]["authorization"]["status"], "pass")
            self.assertEqual({item["rule_id"] for item in report["policy"]["authorization"]["decisions"]}, {"platform-preflight", "owned-preflight"})
            delegated = registry.preflight(snapshot, ["README.md"], [root], authorization_context={
                "subject": {"id": "agent.release", "roles": ["developer"], "teams": ["release"], "attributes": {}},
                "action": "preflight",
                "delegation": {"delegated_by": "platform"},
            })
            self.assertEqual(delegated["policy"]["authorization"]["status"], "pass")
            self.assertEqual(delegated["policy"]["authorization"]["decisions"][0]["rule_id"], "owned-preflight")

    def test_enforced_policy_rejects_unknown_change_without_affected_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root, "https://example.test/service.git")
            snapshot = registry.discover([root], excluded_names=set())
            report = registry.preflight(snapshot, ["outside/unknown.txt"], [root], policy_mode="enforced")
            self.assertEqual(report["policy"]["status"], "fail")
            self.assertTrue(report["policy"]["enforced_failure"])
            self.assertEqual(report["policy"]["exit_code"], 1)
            self.assertTrue(any(check["rule"] == "deny_unknown_changes" for check in report["policy"]["checks"]))

    def test_handoff_from_preflight_does_not_require_workspace_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            evidence = base / "preflight.json"
            evidence.write_text(json.dumps({
                "changes": ["service/api.yaml"],
                "affected_projects": [],
                "risk": {"level": "low", "approval_required": False, "signals": []},
                "policy": {"mode": "advisory", "status": "pass", "enforced_failure": False},
                "required_validation": [],
                "unknowns": [],
                "evidence": {"evidence_id": "evidence.test"},
            }), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(Path(__file__).parent / "aine_registry.py"),
                "handoff", "--root", str(base / "does-not-exist"), "--preflight", str(evidence),
            ], capture_output=True, text=True, check=True)
            handoff = json.loads(result.stdout)
            self.assertEqual(handoff["schema"], "aine.handoff.v1")
            self.assertEqual(handoff["evidence_id"], "evidence.test")

    def test_excluded_project_is_not_active(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "side-projects"
            make_git_project(root / "archived-project", "https://example.test/archived-project.git")
            make_git_project(root / "active", "https://example.test/active.git")
            snapshot = registry.discover([root], excluded_names={"archived-project"})
            self.assertEqual([p["name"] for p in snapshot["projects"]], ["active"])
            self.assertEqual(snapshot["excluded_projects"][0]["name"], "archived-project")

    def test_transitive_cross_root_impact(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            core = base / "core"
            side = base / "side-projects"
            make_git_project(core / "provider", "https://example.test/provider.git")
            make_git_project(side / "middle", "https://example.test/middle.git")
            make_git_project(side / "consumer", "https://example.test/consumer.git")
            # The scanner resolves absolute references to project roots.  This
            # models a provider -> consumer chain without requiring a package
            # manager or network service.
            (side / "middle" / "README.md").write_text(str((core / "provider").resolve()) + "\n", encoding="utf-8")
            (side / "consumer" / "README.md").write_text(str((side / "middle").resolve()) + "\n", encoding="utf-8")
            snapshot = registry.discover([core, side], excluded_names=set())
            provider = next(p for p in snapshot["projects"] if p["name"] == "provider")
            result = registry.impact(snapshot, provider["project_id"])
            self.assertTrue(result["cross_root"])
            self.assertTrue(result["direct_edges"])

    def test_portfolio_relationships_do_not_expand_change_impact_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "provider", "https://example.test/provider.git", {"README.md": "provider\n"})
            make_git_project(root / "governed", "https://example.test/governed.git", {"README.md": "governed\n"})
            (root / "governed" / ".aine").mkdir()
            (root / "governed" / ".aine" / "registry.json").write_text(json.dumps({
                "relationships": [{"target": "workspace.provider", "relationship_type": "portfolio_snapshot_consumer", "kind": "governance", "status": "planned"}],
            }), encoding="utf-8")
            snapshot = registry.discover([root], excluded_names=set())
            provider = registry.impact(snapshot, "workspace.provider")
            self.assertFalse(provider["direct_edges"])


class RelationshipOverlayTests(unittest.TestCase):
    """A relationship whose target must not be published with the source repo.

    A project manifest is committed with its project, so naming a private
    project there publishes that project's existence. The overlay keeps the
    declaration in local configuration and merges it at discovery time.
    """

    def test_overlay_declares_an_edge_the_manifest_never_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "facade", "https://example.test/facade.git", {"README.md": "facade\n"})
            make_git_project(root / "engine", "https://example.test/engine.git", {"README.md": "engine\n"})
            manifest = root / "facade" / ".aine" / "registry.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({"project": {"owner": "platform"}}), encoding="utf-8")
            snapshot = registry.discover([root], excluded_names=set(), relationship_overlays=[
                {"project": "workspace.facade", "relationships": [
                    {"target": "workspace.engine", "relationship_type": "implements", "kind": "governance"},
                ]},
            ])
            self.assertNotIn("engine", manifest.read_text(encoding="utf-8"))
            overlay_edges = [e for e in snapshot["relationships"] if e.get("relationship_source") == "overlay"]
            self.assertEqual(len(overlay_edges), 1)
            edge = overlay_edges[0]
            self.assertEqual(edge["source"]["project_id"], "workspace.facade")
            self.assertEqual(edge["target"]["project_id"], "workspace.engine")
            self.assertEqual(edge["relationship_type"], "implements")
            self.assertEqual(edge["evidence"], ["<local-overlay>"])
            self.assertEqual(registry.snapshot_validation_errors(snapshot), [])

    def test_overlay_applies_to_a_project_that_has_no_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "facade", "https://example.test/facade.git", {"README.md": "facade\n"})
            make_git_project(root / "engine", "https://example.test/engine.git", {"README.md": "engine\n"})
            self.assertFalse((root / "facade" / ".aine").exists())
            snapshot = registry.discover([root], excluded_names=set(), relationship_overlays=[
                {"project": "facade", "relationships": [{"target": "workspace.engine", "relationship_type": "implements"}]},
            ])
            self.assertEqual([e["target"]["project_id"] for e in snapshot["relationships"]], ["workspace.engine"])

    def test_overlay_edge_carries_no_local_path_into_a_portable_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "facade", "https://example.test/facade.git", {"README.md": "facade\n"})
            make_git_project(root / "engine", "https://example.test/engine.git", {"README.md": "engine\n"})
            snapshot = registry.discover([root], excluded_names=set(), relationship_overlays=[
                {"project": "workspace.facade", "relationships": [{"target": "workspace.engine", "relationship_type": "implements"}]},
            ])
            self.assertTrue(registry.no_absolute_paths(snapshot))
            self.assertNotIn(str(root), json.dumps(snapshot))

    def test_overlay_relationship_opts_into_impact_the_same_way_a_manifest_does(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "facade", "https://example.test/facade.git", {"README.md": "facade\n"})
            make_git_project(root / "engine", "https://example.test/engine.git", {"README.md": "engine\n"})
            overlay = [{"project": "workspace.facade", "relationships": [
                {"target": "workspace.engine", "relationship_type": "implements", "kind": "governance"},
            ]}]
            without = registry.discover([root], excluded_names=set(), relationship_overlays=overlay)
            self.assertFalse(registry.impact(without, "workspace.engine")["direct_edges"])
            overlay[0]["relationships"][0]["impact"] = True
            with_impact = registry.discover([root], excluded_names=set(), relationship_overlays=overlay)
            self.assertEqual(
                [e["source"]["project_id"] for e in registry.impact(with_impact, "workspace.engine")["direct_edges"]],
                ["workspace.facade"],
            )

    def test_unresolvable_overlay_project_is_ignored_rather_than_invented(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "facade", "https://example.test/facade.git", {"README.md": "facade\n"})
            snapshot = registry.discover([root], excluded_names=set(), relationship_overlays=[
                {"project": "workspace.gone", "relationships": [{"target": "workspace.facade"}]},
                {"project": "workspace.facade", "relationships": ["not-an-object", {"kind": "governance"}]},
            ])
            self.assertEqual(snapshot["relationships"], [])
            self.assertEqual(registry.snapshot_validation_errors(snapshot), [])

    def test_cli_reads_relationship_overlays_from_local_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "facade", "https://example.test/facade.git", {"README.md": "facade\n"})
            make_git_project(root / "engine", "https://example.test/engine.git", {"README.md": "engine\n"})
            config = Path(temp) / "portfolio.local.json"
            config.write_text(json.dumps({
                "portfolio": {"name": "test"},
                "workspace_roots": [{"id": "workspace", "path": str(root)}],
                "relationship_overlays": [
                    {"project": "workspace.facade", "relationships": [
                        {"target": "workspace.engine", "relationship_type": "implements"},
                    ]},
                ],
            }), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(Path(__file__).parent / "aine_registry.py"),
                "relationships", "--config", str(config),
            ], capture_output=True, text=True, check=True)
            edges = json.loads(result.stdout)
            self.assertEqual([e["relationship_source"] for e in edges], ["overlay"])
            self.assertEqual(edges[0]["target"]["project_id"], "workspace.engine")


class DeclaredProjectIdTests(unittest.TestCase):
    """`project.id` is part of the manifest contract and names peers.

    A derived project ID moves with the workspace root topology a run is
    configured for. A manifest cannot know that topology, so it declares a
    fixed identifier and names its peers by the same. The declared identifier
    resolves references; it never replaces the derived identity.
    """

    @staticmethod
    def build(temp: str, facade_manifest: dict | None, engine_manifest: dict | None) -> Path:
        root = Path(temp) / "workspace"
        for name, manifest in (("facade", facade_manifest), ("engine", engine_manifest)):
            make_git_project(root / name, f"https://example.test/{name}.git", {"README.md": f"{name}\n"})
            if manifest is not None:
                (root / name / ".aine").mkdir()
                (root / name / ".aine" / "registry.json").write_text(json.dumps(manifest), encoding="utf-8")
        return root

    def test_declared_id_resolves_a_target_the_derived_id_would_not_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(
                temp,
                {"relationships": [{"target": "tools.engine", "relationship_type": "implements"}]},
                {"project": {"id": "tools.engine"}},
            )
            snapshot = registry.discover([root], excluded_names=set())
            self.assertEqual([e["target"]["project_id"] for e in snapshot["relationships"]], ["workspace.engine"])
            self.assertEqual(snapshot["relationships"][0]["scope"], "intra_root")
            self.assertEqual([f for f in snapshot["findings"] if f["finding_id"] == "PRJ-001"], [])

    def test_declared_id_is_an_alias_and_not_the_project_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, None, {"project": {"id": "tools.engine"}})
            snapshot = registry.discover([root], excluded_names=set())
            engine = next(p for p in snapshot["projects"] if p["name"] == "engine")
            self.assertEqual(engine["project_id"], "workspace.engine")
            self.assertEqual(engine["declared_id"], "tools.engine")
            facade = next(p for p in snapshot["projects"] if p["name"] == "facade")
            self.assertNotIn("declared_id", facade)

    def test_an_id_declared_by_two_projects_is_reported_and_not_honored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(
                temp,
                {"project": {"id": "tools.shared"}, "relationships": [{"target": "tools.shared", "relationship_type": "implements"}]},
                {"project": {"id": "tools.shared"}},
            )
            snapshot = registry.discover([root], excluded_names=set())
            self.assertEqual([e["target"]["project_id"] for e in snapshot["relationships"]], ["external:tools.shared"])
            conflicts = [f for f in snapshot["findings"] if f["finding_id"] == "PRJ-001"]
            self.assertEqual(len(conflicts), 1)
            self.assertIn("more than one project", conflicts[0]["message"])
            self.assertEqual(conflicts[0]["subject"], "tools.shared")
            self.assertEqual(conflicts[0]["evidence"], ["engine/.aine/registry.json", "facade/.aine/registry.json"])
            self.assertEqual([p["name"] for p in snapshot["projects"] if "declared_id" in p], [])

    def test_a_declared_id_may_not_shadow_another_projects_derived_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(
                temp,
                {"relationships": [{"target": "workspace.facade", "relationship_type": "self_reference"}]},
                {"project": {"id": "workspace.facade"}},
            )
            snapshot = registry.discover([root], excluded_names=set())
            self.assertEqual([e["target"]["project_id"] for e in snapshot["relationships"]], ["workspace.facade"])
            conflicts = [f for f in snapshot["findings"] if f["finding_id"] == "PRJ-001"]
            self.assertEqual(len(conflicts), 1)
            self.assertIn("shadows", conflicts[0]["message"])

    def test_a_declared_id_may_not_shadow_another_projects_name(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, None, {"project": {"id": "facade"}})
            snapshot = registry.discover([root], excluded_names=set())
            conflicts = [f for f in snapshot["findings"] if f["finding_id"] == "PRJ-001"]
            self.assertEqual([c["subject"] for c in conflicts], ["facade"])
            self.assertEqual([p["name"] for p in snapshot["projects"] if "declared_id" in p], [])

    def test_a_declared_id_matching_the_projects_own_name_is_honored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, None, {"project": {"id": "engine"}})
            snapshot = registry.discover([root], excluded_names=set())
            self.assertEqual([f for f in snapshot["findings"] if f["finding_id"] == "PRJ-001"], [])
            engine = next(p for p in snapshot["projects"] if p["name"] == "engine")
            self.assertEqual(engine["declared_id"], "engine")

    def test_an_overlay_may_name_its_project_by_the_declared_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, {"project": {"id": "tools.facade"}}, {"project": {"id": "tools.engine"}})
            snapshot = registry.discover([root], excluded_names=set(), relationship_overlays=[
                {"project": "tools.facade", "relationships": [{"target": "tools.engine", "relationship_type": "implements"}]},
            ])
            edge = snapshot["relationships"][0]
            self.assertEqual(edge["source"]["project_id"], "workspace.facade")
            self.assertEqual(edge["target"]["project_id"], "workspace.engine")
            self.assertEqual(edge["relationship_source"], "overlay")

    def test_impact_queries_accept_the_declared_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, None, {"project": {"id": "tools.engine"}})
            snapshot = registry.discover([root], excluded_names=set())
            self.assertEqual([p["name"] for p in registry.impact(snapshot, "tools.engine")["matched_projects"]], ["engine"])


class PublicationDisclosureTests(unittest.TestCase):
    """A published manifest must not name a project that is not published.

    This is the gate the overlay exists to satisfy: the same edge is a finding
    when the manifest declares it and silent when the overlay does.
    """

    @staticmethod
    def build(temp, facade_relationships):
        root = Path(temp) / "workspace"
        make_git_project(root / "facade", "https://example.test/facade.git", {"README.md": "facade\n"})
        make_git_project(root / "engine", "https://example.test/engine.git", {"README.md": "engine\n"})
        manifest = root / "facade" / ".aine" / "registry.json"
        manifest.parent.mkdir()
        manifest.write_text(json.dumps({"project": {"owner": "platform"}, "relationships": facade_relationships}), encoding="utf-8")
        return root

    @staticmethod
    def disclosures(snapshot):
        return [f for f in snapshot["findings"] if f["finding_id"] == "PRJ-002"]

    def test_published_manifest_naming_an_unpublished_project_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, [{"target": "workspace.engine", "relationship_type": "implements", "kind": "governance"}])
            snapshot = registry.discover([root], excluded_names=set(), published_projects=["workspace.facade"])
            found = self.disclosures(snapshot)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["severity"], "high")
            self.assertIn("workspace.engine", found[0]["message"])
            self.assertEqual(found[0]["evidence"], ["facade/.aine/registry.json"])

    def test_the_same_edge_declared_as_an_overlay_is_not_a_disclosure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, [])
            snapshot = registry.discover(
                [root], excluded_names=set(),
                relationship_overlays=[{"project": "workspace.facade", "relationships": [
                    {"target": "workspace.engine", "relationship_type": "implements", "kind": "governance"}]}],
                published_projects=["workspace.facade"],
            )
            self.assertEqual(len(snapshot["relationships"]), 1)
            self.assertEqual(self.disclosures(snapshot), [])

    def test_an_unpublished_project_may_name_anything_in_its_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, [{"target": "workspace.engine", "relationship_type": "implements", "kind": "governance"}])
            snapshot = registry.discover([root], excluded_names=set(), published_projects=["workspace.engine"])
            self.assertEqual(self.disclosures(snapshot), [])

    def test_an_edge_between_two_published_projects_is_not_a_disclosure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, [{"target": "workspace.engine", "relationship_type": "implements", "kind": "governance"}])
            snapshot = registry.discover([root], excluded_names=set(), published_projects=["workspace.facade", "workspace.engine"])
            self.assertEqual(self.disclosures(snapshot), [])

    def test_the_check_is_inert_until_publication_is_declared(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, [{"target": "workspace.engine", "relationship_type": "implements", "kind": "governance"}])
            self.assertEqual(self.disclosures(registry.discover([root], excluded_names=set())), [])

    def test_cli_reads_published_projects_from_local_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, [{"target": "workspace.engine", "relationship_type": "implements", "kind": "governance"}])
            config = Path(temp) / "portfolio.local.json"
            config.write_text(json.dumps({
                "portfolio": {"name": "test"},
                "workspace_roots": [{"id": "workspace", "path": str(root)}],
                "published_projects": ["workspace.facade"],
            }), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(Path(__file__).parent / "aine_registry.py"),
                "findings", "--config", str(config),
            ], capture_output=True, text=True, check=True)
            reported = [f for f in json.loads(result.stdout) if f["finding_id"] == "PRJ-002"]
            self.assertEqual(len(reported), 1)
            self.assertEqual(reported[0]["evidence"], ["facade/.aine/registry.json"])


class LegacyRelationshipDeclarationTests(unittest.TestCase):
    """Edges declared in `manifest.yaml` are reported, not read.

    The loose descriptor predates the registry manifest and no tool resolves it,
    so an edge left there drifts from the one the registry knows about. The check
    names the descriptor; it does not try to interpret it.
    """

    @staticmethod
    def build(temp, descriptor):
        root = Path(temp) / "workspace"
        files = {"README.md": "engine\n"}
        if descriptor is not None:
            files["manifest.yaml"] = descriptor
        make_git_project(root / "engine", "https://example.test/engine.git", files)
        return root

    @staticmethod
    def legacy(snapshot):
        return [f for f in snapshot["findings"] if f["finding_id"] == "REL-003"]

    def test_a_descriptor_declaring_depends_on_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, "layer: platform\ndepends_on:\n  - repo: other\n")
            found = self.legacy(registry.discover([root], excluded_names=set()))
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["severity"], "medium")
            self.assertEqual(found[0]["subject"], "workspace.engine")
            self.assertEqual(found[0]["evidence"], ["engine/manifest.yaml"])

    def test_a_descriptor_without_relationships_is_not_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, "layer: platform\nsummary: an engine\n")
            self.assertEqual(self.legacy(registry.discover([root], excluded_names=set())), [])

    def test_a_project_without_a_descriptor_is_not_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, None)
            self.assertEqual(self.legacy(registry.discover([root], excluded_names=set())), [])

    def test_a_nested_key_named_depends_on_is_not_a_declaration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, "notes:\n  depends_on: described in prose\n")
            self.assertEqual(self.legacy(registry.discover([root], excluded_names=set())), [])

    def test_the_registry_manifest_does_not_excuse_the_descriptor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, "depends_on:\n  - repo: other\n")
            manifest = root / "engine" / ".aine" / "registry.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({"project": {"owner": "platform"}}), encoding="utf-8")
            self.assertEqual(len(self.legacy(registry.discover([root], excluded_names=set()))), 1)

    def test_cli_reports_the_descriptor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, "depends_on:\n  - repo: other\n")
            config = Path(temp) / "portfolio.local.json"
            config.write_text(json.dumps({
                "portfolio": {"name": "test"},
                "workspace_roots": [{"id": "workspace", "path": str(root)}],
            }), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(Path(__file__).parent / "aine_registry.py"),
                "findings", "--config", str(config),
            ], capture_output=True, text=True, check=True)
            reported = [f for f in json.loads(result.stdout) if f["finding_id"] == "REL-003"]
            self.assertEqual(len(reported), 1)
            self.assertEqual(reported[0]["evidence"], ["engine/manifest.yaml"])


class DanglingDeclaredTargetTests(unittest.TestCase):
    """A declared edge whose target no project answers to is a broken reference.

    Resolution coerces an unmatched target to an `external:` provider so the
    edge stays recordable, but a typo in a hand-written `target` is not a
    third-party provider: reported as DEP-001 info it would sit among ordinary
    package imports and never surface.
    """

    @staticmethod
    def build(temp, manifest_body):
        root = Path(temp) / "workspace"
        make_git_project(root / "engine", "https://example.test/engine.git", {"README.md": "engine\n"})
        make_git_project(root / "consumer", "https://example.test/consumer.git", {"README.md": "consumer\n"})
        if manifest_body is not None:
            manifest = root / "consumer" / ".aine" / "registry.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps(manifest_body), encoding="utf-8")
        return root

    @staticmethod
    def dangling(snapshot):
        return [f for f in snapshot["findings"] if f["finding_id"] == "REL-004"]

    def test_a_relationship_target_no_project_answers_to_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, {"relationships": [{"target": "workspace.enginee", "relationship_type": "snapshot_consumer", "kind": "governance"}]})
            snapshot = registry.discover([root], excluded_names=set())
            found = self.dangling(snapshot)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["severity"], "medium")
            self.assertEqual(found[0]["status"], "dangling")
            self.assertIn("workspace.enginee", found[0]["message"])
            self.assertEqual(found[0]["evidence"], ["consumer/.aine/registry.json"])

    def test_the_dangling_edge_is_not_also_downgraded_to_dep_001(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, {"relationships": [{"target": "workspace.enginee", "relationship_type": "snapshot_consumer", "kind": "governance"}]})
            snapshot = registry.discover([root], excluded_names=set())
            subject = self.dangling(snapshot)[0]["subject"]
            dep = [f for f in snapshot["findings"] if f["finding_id"] == "DEP-001" and f["subject"] == subject]
            self.assertEqual(dep, [])

    def test_a_declared_dependency_target_is_covered_too(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, {"dependencies": [{"target": "workspace.ghost", "kind": "runtime_api", "status": "active"}]})
            found = self.dangling(registry.discover([root], excluded_names=set()))
            self.assertEqual(len(found), 1)
            self.assertIn("workspace.ghost", found[0]["message"])

    def test_a_target_that_resolves_is_not_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, {"relationships": [{"target": "workspace.engine", "relationship_type": "snapshot_consumer", "kind": "governance"}]})
            self.assertEqual(self.dangling(registry.discover([root], excluded_names=set())), [])

    def test_a_target_that_resolves_by_name_is_not_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, {"relationships": [{"target": "engine", "relationship_type": "snapshot_consumer", "kind": "governance"}]})
            self.assertEqual(self.dangling(registry.discover([root], excluded_names=set())), [])

    def test_an_explicit_external_provider_stays_with_dep_001(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, {"dependencies": [{"target": "external:redis", "kind": "runtime_api", "status": "active"}]})
            snapshot = registry.discover([root], excluded_names=set())
            self.assertEqual(self.dangling(snapshot), [])
            self.assertTrue(any(f["finding_id"] == "DEP-001" and "external:redis" in f["message"] for f in snapshot["findings"]))

    def test_an_overlay_declared_dangling_target_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, None)
            snapshot = registry.discover([root], excluded_names=set(), relationship_overlays=[
                {"project": "workspace.consumer", "relationships": [{"target": "workspace.ghost", "relationship_type": "snapshot_consumer", "kind": "governance"}]},
            ])
            found = self.dangling(snapshot)
            self.assertEqual(len(found), 1)
            self.assertIn("workspace.ghost", found[0]["message"])
            self.assertEqual(found[0]["evidence"], ["<local-overlay>"])

    def test_cli_reports_the_dangling_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, {"relationships": [{"target": "workspace.enginee", "relationship_type": "snapshot_consumer", "kind": "governance"}]})
            config = Path(temp) / "portfolio.local.json"
            config.write_text(json.dumps({
                "portfolio": {"name": "test"},
                "workspace_roots": [{"id": "workspace", "path": str(root)}],
            }), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(Path(__file__).parent / "aine_registry.py"),
                "findings", "--config", str(config),
            ], capture_output=True, text=True, check=True)
            reported = [f for f in json.loads(result.stdout) if f["finding_id"] == "REL-004"]
            self.assertEqual(len(reported), 1)
            self.assertEqual(reported[0]["evidence"], ["consumer/.aine/registry.json"])


class DeclaredArtifactExistenceTests(unittest.TestCase):
    """A manifest may not declare a file present that is not there.

    Everything a manifest hangs off an artifact — a source-of-truth authority,
    a high-risk path, an approval gate — is void when the file is absent, and
    nothing else in discovery reads the declared path back against the tree.
    """

    @staticmethod
    def build(temp, artifacts, files=None):
        root = Path(temp) / "workspace"
        make_git_project(root / "engine", "https://example.test/engine.git", {"README.md": "engine\n", **(files or {})})
        manifest = root / "engine" / ".aine" / "registry.json"
        manifest.parent.mkdir()
        manifest.write_text(json.dumps({"project": {"owner": "platform"}, "artifacts": artifacts}), encoding="utf-8")
        return root

    @staticmethod
    def missing(snapshot):
        return [f for f in snapshot["findings"] if f["finding_id"] == "ART-001"]

    def test_a_declared_present_artifact_that_is_absent_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, [{"id": "gone", "path": "src/gone.py", "status": "present"}])
            found = self.missing(registry.discover([root], excluded_names=set()))
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["severity"], "high")
            self.assertEqual(found[0]["subject"], "gone")
            self.assertIn("engine/src/gone.py", found[0]["message"])
            self.assertEqual(found[0]["evidence"], ["engine/.aine/registry.json"])

    def test_a_declared_present_artifact_that_exists_is_not_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, [{"id": "here", "path": "src/here.py", "status": "present"}], {"src/here.py": "x = 1\n"})
            self.assertEqual(self.missing(registry.discover([root], excluded_names=set())), [])

    def test_a_declared_present_directory_counts_as_existing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, [{"id": "tree", "path": "src", "status": "present"}], {"src/here.py": "x = 1\n"})
            self.assertEqual(self.missing(registry.discover([root], excluded_names=set())), [])

    def test_an_artifact_not_declared_present_is_not_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, [{"id": "later", "path": "src/later.py", "status": "planned"}])
            self.assertEqual(self.missing(registry.discover([root], excluded_names=set())), [])

    def test_the_check_does_not_reach_adapter_discovered_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root / "engine", "https://example.test/engine.git", {"README.md": "engine\n"})
            self.assertEqual(self.missing(registry.discover([root], excluded_names=set())), [])

    def test_cli_reports_the_missing_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp, [{"id": "gone", "path": "src/gone.py", "status": "present"}])
            config = Path(temp) / "portfolio.local.json"
            config.write_text(json.dumps({
                "portfolio": {"name": "test"},
                "workspace_roots": [{"id": "workspace", "path": str(root)}],
            }), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(Path(__file__).parent / "aine_registry.py"),
                "findings", "--config", str(config),
            ], capture_output=True, text=True, check=True)
            reported = [f for f in json.loads(result.stdout) if f["finding_id"] == "ART-001"]
            self.assertEqual(len(reported), 1)
            self.assertEqual(reported[0]["evidence"], ["engine/.aine/registry.json"])


class NestedGitRootAttributionTests(unittest.TestCase):
    """A checkout nested inside another checkout owns its own files.

    Both directions are asserted. False attribution is an umbrella claiming a
    child's artifacts, imports, or language. Evidence loss is the umbrella
    losing files it does own, which a pure absence check cannot catch. The
    assertions compare whole sorted lists rather than counts: a count survives
    a swap, and `MAX_ARTIFACTS_PER_PROJECT` truncates silently, so an umbrella
    that absorbs its children can reach the cap and drop its own records with
    no error raised anywhere.
    """

    @staticmethod
    def build(temp: str) -> Path:
        root = Path(temp) / "workspace"
        make_git_project(root / "umbrella", "https://example.test/umbrella.git", {
            "specs-manifest.json": json.dumps({"owner": "umbrella"}),
            "parent_module.py": "import parent_only_marker\n",
            "notes.md": "see ../sibling/parent-edge\n",
        })
        make_git_project(root / "umbrella" / "child", "https://example.test/child.git", {
            "specs-manifest.json": json.dumps({"owner": "child"}),
            "child_module.py": "import child_only_marker\n",
            "notes.md": "see ../sibling/child-edge\n",
            "package.json": json.dumps({"name": "child"}),
        })
        make_git_project(root / "sibling", "https://example.test/sibling.git", {"README.md": "sibling\n"})
        return root

    @staticmethod
    def project_id(snapshot: dict, path: str) -> str:
        return next(p["project_id"] for p in snapshot["projects"] if p["path"] == path)

    def snapshot(self, temp: str) -> dict:
        return registry.discover([self.build(temp)], excluded_names=set())

    def test_both_checkouts_are_discovered_as_separate_projects(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = self.snapshot(temp)
            self.assertEqual(
                sorted(p["path"] for p in snapshot["projects"]),
                ["sibling", "umbrella", "umbrella/child"],
            )

    def test_artifacts_are_attributed_to_the_owning_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = self.snapshot(temp)
            umbrella = self.project_id(snapshot, "umbrella")
            child = self.project_id(snapshot, "umbrella/child")
            self.assertEqual(
                sorted(a["path"] for a in snapshot["artifacts"] if a["project_id"] == umbrella),
                ["specs-manifest.json"],
                "the umbrella must keep its own artifact and claim none of the child's",
            )
            self.assertEqual(
                sorted(a["path"] for a in snapshot["artifacts"] if a["project_id"] == child),
                ["specs-manifest.json"],
                "the child's artifact must survive pruning, relative to the child",
            )

    def test_imports_are_attributed_to_the_owning_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = self.snapshot(temp)
            umbrella = self.project_id(snapshot, "umbrella")
            child = self.project_id(snapshot, "umbrella/child")
            by_project = {}
            for record in snapshot["imports"]:
                by_project.setdefault(record["source_project_id"], []).append(
                    (record["source_path"], record["specifier"])
                )
            self.assertEqual(sorted(by_project.get(umbrella, [])), [("parent_module.py", "parent_only_marker")])
            self.assertEqual(sorted(by_project.get(child, [])), [("child_module.py", "child_only_marker")])

    def test_language_detection_stops_at_the_git_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = self.snapshot(temp)
            languages = {
                p["path"]: set(p["runtime"]["languages"]) for p in snapshot["projects"]
            }
            self.assertIn("python", languages["umbrella"], "the umbrella owns parent_module.py")
            self.assertNotIn(
                "javascript/typescript", languages["umbrella"],
                "only the child holds a package.json; the umbrella must not inherit its language",
            )
            self.assertIn("javascript/typescript", languages["umbrella/child"])

    def test_parent_owned_files_beside_a_child_root_remain_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build(temp)
            nested = Path(temp) / "workspace" / "umbrella" / "child"
            (root / "umbrella" / "courses.generated.json").write_text("{}", encoding="utf-8")
            snapshot = registry.discover([root], excluded_names=set())
            umbrella = self.project_id(snapshot, "umbrella")
            self.assertTrue(nested.is_dir(), "the fixture must still nest a real checkout")
            self.assertEqual(
                sorted(a["path"] for a in snapshot["artifacts"] if a["project_id"] == umbrella),
                ["courses.generated.json", "specs-manifest.json"],
                "pruning a child root must not remove sibling files the parent owns",
            )


if __name__ == "__main__":
    unittest.main()


INVENTORY_DOCUMENT = """schema: core.portfolio.inventory.v1
observed_at: 2026-01-01
revised: 2026-01-02
topology: multi-root-polyrepo

workspace_roots:
  - id: ws
    logical_path: ws
    lifecycle: active
    registry_included: true
  - id: attic
    logical_path: attic
    lifecycle: quarantined
    registry_included: false

repositories:
  active_core:
    - path: ws/alpha
    - path: ws/beta
      role: umbrella

vendored_git_checkouts:
  - path: ws/vendor
    owner: ws/beta
    classification: ignored-runtime-dependency

non_projects:
  - path: attic/fragment
    classification: incomplete-orphan-fragment
    git_repository: false

counts:
  active_core_git_roots: 2
"""


class InventoryReaderTests(unittest.TestCase):
    """The reader is hand-rolled, so its refusals are the thing worth testing.

    Registry declares no dependencies, so the inventory is parsed rather than
    loaded by a YAML library. The risk that carries is a silent misread: a
    document the reader half-understands would produce a plausible-looking but
    wrong classification. Each case below is a construct the reader must refuse
    outright rather than interpret.
    """

    def test_supported_shape_round_trips(self):
        document = inventory.parse_document(INVENTORY_DOCUMENT)
        self.assertEqual(document["schema"], "core.portfolio.inventory.v1")
        self.assertEqual(document["counts"], {"active_core_git_roots": 2})
        self.assertEqual(
            document["repositories"]["active_core"],
            [{"path": "ws/alpha"}, {"path": "ws/beta", "role": "umbrella"}],
        )
        self.assertEqual(document["workspace_roots"][0]["registry_included"], True)
        self.assertEqual(document["non_projects"][0]["git_repository"], False)

    def test_unsupported_constructs_raise_instead_of_being_guessed(self):
        cases = {
            "tab indentation": "schema: x\nkey:\n\t- path: a\n",
            "odd indentation": "schema: x\nkey:\n   sub: a\n",
            "flow sequence": "schema: x\nkey: [a, b]\n",
            "flow mapping": "schema: x\nkey: {a: b}\n",
            "anchor": "schema: x\nkey: &anchor a\n",
            "quoted scalar": "schema: x\nkey: 'a'\n",
            "block scalar": "schema: x\nkey: |\n  text\n",
            "trailing comment": "schema: x\nkey: a # note\n",
            "missing space after colon": "schema: x\nkey:a\n",
            "duplicate key": "schema: x\nkey: a\nkey: b\n",
            "duplicate key in item": "schema: x\nkey:\n  - path: a\n    path: b\n",
            "key without value or block": "schema: x\nkey:\n",
            "bare sequence item": "schema: x\nkey:\n  - a\n",
            "nested block in sequence item": "schema: x\nkey:\n  - path: a\n    sub:\n      deep: b\n",
            "empty document": "\n# only a comment\n",
        }
        for name, text in cases.items():
            with self.subTest(name):
                with self.assertRaises(inventory.InventoryFormatError):
                    inventory.parse_document(text)

    def test_wrong_schema_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "inventory.yaml"
            path.write_text("schema: some.other.v9\nworkspace_roots:\n  - id: a\n    logical_path: a\n", encoding="utf-8")
            with self.assertRaises(inventory.InventoryFormatError):
                inventory.load(path)


class InventoryJoinTests(unittest.TestCase):
    """Lifecycle comes from the portfolio's decision, not from the filesystem.

    Discovery can see that a checkout exists; it cannot see that the checkout is
    a vendored runtime dependency rather than a maintained project. These tests
    fix the join's two obligations: apply what the inventory declares, and report
    every disagreement in both directions instead of absorbing it.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.workspace = self.base / "ws"
        for name in ("alpha", "beta", "vendor"):
            make_git_project(self.workspace / name, f"https://example.com/{name}.git", {"README.md": name})
        self.inventory_path = self.base / "inventory.yaml"
        self.inventory_path.write_text(INVENTORY_DOCUMENT, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, text: str | None = None) -> dict:
        if text is not None:
            self.inventory_path.write_text(text, encoding="utf-8")
        return inventory.load(self.inventory_path)

    def snapshot(self, text: str | None = None) -> dict:
        return registry.discover([self.workspace], excluded_names=set(), inventory=self.load(text))

    def classification_of(self, snapshot: dict, name: str) -> dict:
        return next(item["classification"] for item in snapshot["projects"] if item["name"] == name)

    def test_base_is_derived_from_the_rows_not_assumed(self):
        joined = inventory.classifications(self.load(), [self.workspace])
        self.assertEqual(joined["base"], self.base)
        self.assertEqual(sorted(joined["index"]), ["ws/alpha", "ws/beta", "ws/vendor"])

    def test_declared_classification_reaches_the_project_record(self):
        snapshot = self.snapshot()
        self.assertEqual(self.classification_of(snapshot, "alpha")["lifecycle"], "active")
        self.assertEqual(self.classification_of(snapshot, "beta")["role"], "umbrella")
        vendor = self.classification_of(snapshot, "vendor")
        self.assertEqual(vendor["lifecycle"], "vendored")
        self.assertEqual(vendor["owner"], "ws/beta")
        self.assertEqual(snapshot["inventory"]["applied"], {"active": 2, "vendored": 1})

    def test_rows_outside_the_scanned_roots_are_not_reported_as_drift(self):
        snapshot = self.snapshot()
        self.assertEqual(snapshot["inventory"]["declared"], {"rows_in_scope": 3, "rows_out_of_scope": 1})
        self.assertEqual(snapshot["inventory"]["drift"], {"declared_not_discovered": [], "discovered_not_declared": []})
        self.assertEqual([item for item in snapshot["findings"] if item["finding_id"].startswith("INV-")], [])

    def test_a_checkout_declared_under_active_projects_is_classified_active(self):
        snapshot = self.snapshot(INVENTORY_DOCUMENT.replace(
            "  active_core:\n    - path: ws/alpha\n",
            "  active_projects:\n    - path: ws/alpha\n  active_core:\n",
        ))
        alpha = self.classification_of(snapshot, "alpha")
        self.assertEqual(alpha["lifecycle"], "active")
        self.assertEqual(alpha["group"], "active_projects")
        self.assertEqual([item for item in snapshot["findings"] if item["finding_id"].startswith("INV-")], [])

    def test_a_declared_checkout_that_does_not_exist_is_reported(self):
        snapshot = self.snapshot(INVENTORY_DOCUMENT.replace("    - path: ws/alpha\n", "    - path: ws/alpha\n    - path: ws/ghost\n"))
        self.assertEqual(snapshot["inventory"]["drift"]["declared_not_discovered"], ["ws/ghost"])
        finding = next(item for item in snapshot["findings"] if item["finding_id"] == "INV-001")
        self.assertEqual(finding["evidence"], ["ws/ghost"])

    def test_excluding_a_project_does_not_report_its_nested_vendored_checkout_as_missing(self):
        make_git_project(self.workspace / "vendor" / "third_party" / "nested", "https://example.com/nested.git", {"README.md": "nested"})
        text = INVENTORY_DOCUMENT.replace(
            "  - path: ws/vendor\n    owner: ws/beta\n    classification: ignored-runtime-dependency\n",
            "  - path: ws/vendor\n    owner: ws/beta\n    classification: ignored-runtime-dependency\n  - path: ws/vendor/third_party/nested\n    owner: ws/beta\n    classification: ignored-runtime-dependency\n",
        )
        snapshot = registry.discover([self.workspace], excluded_names={"vendor"}, inventory=self.load(text))
        self.assertEqual(snapshot["inventory"]["drift"]["declared_not_discovered"], [])
        self.assertEqual([item for item in snapshot["findings"] if item["finding_id"] == "INV-001"], [])

    def test_excluding_a_project_does_not_report_the_project_itself_as_missing(self):
        snapshot = registry.discover([self.workspace], excluded_names={"vendor"}, inventory=self.load())
        self.assertEqual(snapshot["inventory"]["drift"]["declared_not_discovered"], [])
        self.assertEqual([item for item in snapshot["findings"] if item["finding_id"] == "INV-001"], [])

    def test_excluding_a_project_still_reports_an_unrelated_missing_checkout(self):
        text = INVENTORY_DOCUMENT.replace("    - path: ws/alpha\n", "    - path: ws/alpha\n    - path: ws/ghost\n")
        snapshot = registry.discover([self.workspace], excluded_names={"vendor"}, inventory=self.load(text))
        self.assertEqual(snapshot["inventory"]["drift"]["declared_not_discovered"], ["ws/ghost"])
        finding = next(item for item in snapshot["findings"] if item["finding_id"] == "INV-001")
        self.assertEqual(finding["evidence"], ["ws/ghost"])

    def test_a_discovered_checkout_the_inventory_omits_is_reported(self):
        make_git_project(self.workspace / "gamma", "https://example.com/gamma.git", {"README.md": "gamma"})
        snapshot = self.snapshot()
        self.assertEqual(snapshot["inventory"]["drift"]["discovered_not_declared"], ["ws/gamma"])
        finding = next(item for item in snapshot["findings"] if item["finding_id"] == "INV-002")
        self.assertEqual(finding["evidence"], ["ws/gamma"])
        self.assertEqual(self.classification_of(snapshot, "gamma"), {"lifecycle": "UNKNOWN", "group": "UNKNOWN", "source": "inventory.yaml"})

    def test_without_an_inventory_discovery_classifies_nothing(self):
        snapshot = registry.discover([self.workspace], excluded_names=set())
        self.assertNotIn("inventory", snapshot)
        self.assertEqual([item for item in snapshot["projects"] if "classification" in item], [])

    def test_join_output_stays_portable(self):
        snapshot = self.snapshot()
        self.assertTrue(registry.no_absolute_paths(snapshot["inventory"]))
