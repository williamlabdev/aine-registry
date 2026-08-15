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
