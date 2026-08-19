from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import aine_registry as registry


def make_git_project(path: Path, remote: str, files: dict[str, str] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    for name, content in (files or {}).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


class MultiRootRegistryTests(unittest.TestCase):
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
                "py/app.py": "from .client import Client\nimport requests\n",
                "py/client.py": "class Client: pass\n",
                "web/index.ts": "import { value } from './util';\nconst x = require('lodash');\nconst y = import('@scope/events');\n",
                "web/util.ts": "export const value = 1;\n",
                "cmd/main.go": "package main\nimport (\n  \"fmt\"\n  \"example.com/acme/dep\"\n)\n",
                "src/main.rs": "mod parser;\nuse serde::Deserialize;\n",
                "src/parser.rs": "pub struct Parser;\n",
            })
            snapshot = registry.discover([root], excluded_names=set())
            imports = snapshot["imports"]
            self.assertEqual({item["language"] for item in imports}, {"python", "typescript", "go", "rust"})
            self.assertTrue(any(item["specifier"] == ".client" and item["resolution"] == "local" for item in imports))
            self.assertTrue(any(item["specifier"] == "./util" and item["resolution"] == "local" for item in imports))
            self.assertTrue(any(item["specifier"] == "requests" and item["resolution"] == "external" for item in imports))
            self.assertTrue(any(item["specifier"] == "lodash" and item["resolution"] == "external" for item in imports))
            self.assertTrue(any(item["specifier"] == "lodash" and item["kind"] == "module_import" for item in imports))
            self.assertTrue(any(item["specifier"] == "@scope/events" and item["kind"] == "dynamic_import" for item in imports))
            self.assertTrue(any(item["specifier"] == "fmt" and item["language"] == "go" for item in imports))
            self.assertTrue(any(item["specifier"] == "parser" and item["resolution"] == "local" for item in imports))
            self.assertTrue(any(item["specifier"] == "serde::Deserialize" and item["resolution"] == "external" for item in imports))
            self.assertEqual(registry.snapshot_validation_errors(snapshot), [])

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
                "relationships": [{"target": "workspace.provider", "relationship_type": "event_consumer", "status": "planned"}],
            }), encoding="utf-8")
            snapshot = registry.discover([root], excluded_names=set())
            self.assertTrue(any(a["artifact_id"] == "provider-api" for a in snapshot["artifacts"]))
            self.assertTrue(any(e["target"]["project_id"] == "workspace.provider" and e["kind"] == "runtime_api" for e in snapshot["dependencies"]))
            self.assertTrue(any(e.get("relationship_type") == "event_consumer" and e["status"] == "planned" for e in snapshot["dependencies"]))
            self.assertEqual(len(snapshot["relationships"]), 1)
            result = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "relationships", "--root", str(root)], capture_output=True, text=True, check=True)
            self.assertIn('"relationship_type": "event_consumer"', result.stdout)
            filtered = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "relationships", "--root", str(root), "--relationship-status", "planned"], capture_output=True, text=True, check=True)
            self.assertIn('"relationship_type": "event_consumer"', filtered.stdout)
            empty = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "relationships", "--root", str(root), "--relationship-status", "active"], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(empty.stdout), [])
            context = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "context", "--root", str(root), "--project", "consumer"], capture_output=True, text=True, check=True)
            context_data = json.loads(context.stdout)
            self.assertEqual([item["name"] for item in context_data["projects"]], ["consumer"])
            self.assertEqual(len(context_data["relationships"]), 1)
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

    def test_authorization_context_supports_rbac_and_abac_conditions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            make_git_project(root, "https://example.test/service.git", {"README.md": "service\n"})
            (root / ".aine").mkdir()
            (root / ".aine" / "registry.json").write_text(json.dumps({
                "project": {
                    "policy": {
                        "authorization": {
                            "rules": [{
                                "id": "platform-preflight",
                                "effect": "allow",
                                "actions": ["preflight"],
                                "roles": ["developer"],
                                "conditions": {"subject.attributes.team": "platform", "resource.risk": "low"},
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
            self.assertEqual(report["policy"]["authorization"]["decisions"][0]["rule_id"], "platform-preflight")

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


if __name__ == "__main__":
    unittest.main()
