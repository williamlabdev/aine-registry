"""Shared constants for the AINE Registry modules."""

from __future__ import annotations

import re
from pathlib import Path


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
