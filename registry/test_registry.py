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
                "source_of_truth": [{"domain": "payments.api", "authority": {"project_id": "workspace.provider", "artifact": "provider-api"}}],
            }), encoding="utf-8")
            (consumer / ".aine").mkdir()
            (consumer / ".aine" / "registry.json").write_text(json.dumps({
                "dependencies": [{"target": "workspace.provider", "kind": "runtime_api"}],
            }), encoding="utf-8")
            snapshot = registry.discover([root], excluded_names=set())
            self.assertTrue(any(a["artifact_id"] == "provider-api" for a in snapshot["artifacts"]))
            self.assertTrue(any(e["target"]["project_id"] == "workspace.provider" and e["kind"] == "runtime_api" for e in snapshot["dependencies"]))
            report = registry.preflight(snapshot, ["provider/api.yaml"], [root])
            self.assertEqual([a["artifact_id"] for a in report["matched_artifacts"]], ["provider-api"])
            self.assertIn("workspace.consumer", {p["project_id"] for p in report["affected_projects"]})
            self.assertTrue(report["read_only"])
            self.assertTrue(report["source_of_truth"])

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
            self.assertEqual(report["policy"]["status"], "fail")
            self.assertTrue(any(check["rule"] == "required_checks" for check in report["policy"]["checks"]))
            handoff = subprocess.run([sys.executable, str(Path(__file__).parent / "aine_registry.py"), "handoff", "--preflight", str(evidence)], capture_output=True, text=True, check=True)
            handoff_data = json.loads(handoff.stdout)
            self.assertEqual(handoff_data["schema"], "aine.handoff.v1")
            self.assertEqual(handoff_data["status"], "human_review_required")

    def test_remote_credentials_are_not_exported(self):
        self.assertEqual(registry.normalized_remote("https://token:secret@example.test/org/repo.git"), "https://example.test/org/repo")

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
