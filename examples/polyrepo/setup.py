#!/usr/bin/env python3
"""Initialize the public polyrepo fixture without requiring a shell."""

from __future__ import annotations

from pathlib import Path
import subprocess


PROJECTS = (
    Path("core/checkout-service"),
    Path("core/web-app"),
    Path("side-projects/content-tool"),
)


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    for relative_project in PROJECTS:
        project = base_dir / relative_project
        if (project / ".git").is_dir():
            continue
        subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(project), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "-c",
                "user.name=AINE Example",
                "-c",
                "user.email=example@aine.invalid",
                "commit",
                "-qm",
                "initial example project",
            ],
            check=True,
        )
    print("Initialized three independent example Git projects.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
