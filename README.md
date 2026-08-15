# AINE Registry

Read-only, multi-root portfolio intelligence for AI-assisted software engineering.

AINE Registry discovers the boundaries and relationships an AI coding agent needs before changing a distributed software system:

- projects and Git repositories
- local checkouts and workspace roots
- source and generated artifacts
- typed dependencies with evidence
- portable snapshots and impact queries

It is deliberately a small, local-first core. It does not edit projects, install packages, run generators, deploy, execute agents, or upload source code.

## Requirements

- Python 3.9 or newer
- No third-party Python packages

The repository does not include a virtual environment. A virtual environment is local machine state and must not be committed. It is optional for this dependency-free core, but recommended for isolated development.

## Quick start

Clone the repository and create an optional virtual environment:

```bash
git clone https://github.com/williamlabdev/aine-registry.git
cd aine-registry
python3 -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell, use `.venv\Scripts\Activate.ps1` instead of `source .venv/bin/activate`.

No package installation is required.

Discover one or more workspace roots:

```bash
python3 registry/aine_registry.py --root /path/to/workspace discover
python3 registry/aine_registry.py --root /path/to/core --root /path/to/side-projects portfolio discover --output /tmp/aine-portfolio.json
python3 registry/aine_registry.py --snapshot /tmp/aine-portfolio.json impact --project workspace.app
python3 registry/aine_registry.py --snapshot /tmp/aine-portfolio.json validate
```

The current release is invoked through the Python script. The shorthand commands
`aine scan`, `aine impact`, and `aine preflight` are planned interfaces, not
installed commands in v0.1.0. `preflight` is not implemented yet.

For example, impact analysis against live workspace roots currently uses:

```bash
python3 registry/aine_registry.py \
  --root /path/to/core \
  --root /path/to/side-projects \
  impact --project checkout-service
```

Run the test suite:

```bash
python3 -m unittest discover -s registry -p 'test_*.py' -v
```

## Project documents

- [Vision](docs/VISION.md) — why the registry exists and what it will not do
- [Roadmap](docs/ROADMAP.md) — released capabilities and planned milestones
- [Blueprint](docs/BLUEPRINT.md) — public-core boundaries, data model, and runtime flow

## Public-core boundary

This repository contains generic discovery and graph behavior only. Portfolio-specific project IDs, business metadata, source-of-truth seeds, customer data, local snapshots, and private evidence belong in a project adapter or private workspace configuration.

Absolute local paths are used only during discovery and are redacted from portable snapshots and normal output.

## Status

Experimental public core. Relationship inference is evidence-based and incomplete for dynamic runtime behavior. Unknown relationships remain `UNKNOWN`; they are not silently treated as absent.

## License

MIT. See [LICENSE](LICENSE).
