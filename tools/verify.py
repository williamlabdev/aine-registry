#!/usr/bin/env python3
"""Run the dependency-free AINE Registry verification gates."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_SCRIPT = ROOT / "registry" / "aine_registry.py"
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from registry.version import VERSION


def run(*args: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, *args]
    return subprocess.run(command, cwd=cwd, check=True, text=True, env=env)


def verify_schemas() -> None:
    paths = sorted((ROOT / "registry" / "schema").glob("*.json"))
    if not paths:
        raise AssertionError("no registry schemas found")
    for path in paths:
        json.loads(path.read_text(encoding="utf-8"))
    print(f"schemas: {len(paths)} parsed")


def verify_cli_contract(env: dict[str, str]) -> None:
    project_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if 'version = {attr = "registry.version.VERSION"}' not in project_text:
        raise AssertionError("pyproject.toml does not use the canonical registry.version.VERSION")
    result = subprocess.run(
        [sys.executable, str(REGISTRY_SCRIPT), "--version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    expected_output = f"{REGISTRY_SCRIPT.name} {VERSION}"
    if result.stdout.strip() != expected_output:
        raise AssertionError(f"unexpected version output: {result.stdout!r}")
    subprocess.run([sys.executable, str(REGISTRY_SCRIPT), "--help"], cwd=ROOT, check=True, stdout=subprocess.DEVNULL, env=env)
    print("cli: version/help passed")


def verify_polyrepo_example(env: dict[str, str]) -> None:
    source = ROOT / "examples" / "polyrepo"
    with tempfile.TemporaryDirectory(prefix="aine-registry-verify-") as temp:
        fixture = Path(temp) / "polyrepo"
        shutil.copytree(source, fixture)
        subprocess.run([sys.executable, str(fixture / "setup.py")], cwd=fixture, check=True, capture_output=True, text=True, env=env)
        snapshot_path = fixture / "snapshot.json"
        subprocess.run(
            [
                sys.executable,
                str(REGISTRY_SCRIPT),
                "discover",
                "--root",
                str(fixture / "core"),
                "--root",
                str(fixture / "side-projects"),
                "--output",
                str(snapshot_path),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if len(snapshot["projects"]) != 3:
            raise AssertionError("polyrepo example should discover three projects")
        if sum(item.get("scope") == "cross_root" for item in snapshot["dependencies"]) != 1:
            raise AssertionError("polyrepo example should discover one cross-root dependency")
        serialized = json.dumps(snapshot, ensure_ascii=False)
        if str(fixture) in serialized:
            raise AssertionError("portable snapshot contains the temporary fixture path")
        validation = subprocess.run(
            [sys.executable, str(REGISTRY_SCRIPT), "validate", "--snapshot", str(snapshot_path)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        validation_payload = json.loads(validation.stdout)
        if validation_payload.get("valid") is not True or validation_payload.get("errors") != []:
            raise AssertionError(f"polyrepo snapshot validation failed: {validation_payload}")
        subprocess.run(
            [
                sys.executable,
                str(REGISTRY_SCRIPT),
                "impact",
                "--snapshot",
                str(snapshot_path),
                "--project",
                "core.checkout-service",
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            env=env,
        )
        subprocess.run(
            [
                sys.executable,
                str(REGISTRY_SCRIPT),
                "preflight",
                "--snapshot",
                str(snapshot_path),
                "--change",
                "core/checkout-service/openapi.yaml",
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            env=env,
        )
    print("polyrepo: discover/validate/impact/preflight passed")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aine-registry-verify-cache-") as cache:
        env = os.environ.copy()
        env["PYTHONPYCACHEPREFIX"] = cache
        run("-m", "unittest", "discover", "-s", "registry", "-p", "test_*.py", "-v", env=env)
        run("-m", "compileall", "-q", "registry", env=env)
        print("compileall: passed")
        verify_schemas()
        verify_cli_contract(env)
        verify_polyrepo_example(env)
    print("AINE Registry verification: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
