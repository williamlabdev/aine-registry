# AINE Registry

Read-only, multi-root portfolio intelligence for AI-assisted software engineering.

AINE Registry discovers the boundaries and relationships an AI coding agent needs before changing a distributed software system:

- projects and Git repositories
- local checkouts and workspace roots
- source and generated artifacts
- typed dependencies with evidence
- portable snapshots and impact queries

It is deliberately a small, local-first core. It does not edit projects, install packages, run generators, deploy, execute agents, or upload source code.

## Quick start

```bash
python3 registry/aine_registry.py --root /path/to/workspace discover
python3 registry/aine_registry.py --root /path/to/core --root /path/to/side-projects portfolio discover --output /tmp/aine-portfolio.json
python3 registry/aine_registry.py --snapshot /tmp/aine-portfolio.json impact --project workspace.app
python3 registry/aine_registry.py --snapshot /tmp/aine-portfolio.json validate
```

Run tests:

```bash
python3 -m unittest discover -s registry -p 'test_*.py' -v
```

## Public-core boundary

This repository contains generic discovery and graph behavior only. Portfolio-specific project IDs, business metadata, source-of-truth seeds, customer data, local snapshots, and private evidence belong in a project adapter or private workspace configuration.

Absolute local paths are used only during discovery and are redacted from portable snapshots and normal output.

## Status

Experimental public core. Relationship inference is evidence-based and incomplete for dynamic runtime behavior. Unknown relationships remain `UNKNOWN`; they are not silently treated as absent.

## License

MIT. See [LICENSE](LICENSE).
