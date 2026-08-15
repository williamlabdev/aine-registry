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

To install the `aine` command in the optional virtual environment:

```bash
python3 -m pip install -e .
aine --help
```

The editable install is only a CLI packaging step; the registry itself has no runtime third-party dependencies.

Discover one or more workspace roots:

```bash
python3 registry/aine_registry.py --root /path/to/workspace discover
python3 registry/aine_registry.py --root /path/to/core --root /path/to/side-projects portfolio discover --output /tmp/aine-portfolio.json
python3 registry/aine_registry.py --snapshot /tmp/aine-portfolio.json impact --project workspace.app
python3 registry/aine_registry.py --snapshot /tmp/aine-portfolio.json validate
```

After installation, the equivalent shorthand is:

```bash
aine scan --root /path/to/workspace
aine impact --root /path/to/workspace --project workspace.app
aine validate --root /path/to/workspace
aine preflight --root /path/to/workspace --change app/api.yaml
aine preflight --root /path/to/workspace --diff --format markdown
aine preflight --root /path/to/workspace --staged
aine preflight --root /path/to/workspace --base origin/main
```

The installed `aine` command is the supported interface in v0.4.0. The Python
script entrypoint remains available for backwards compatibility.

## Project-local metadata

Add an optional `.aine/registry.json` file to a project when relationships
cannot be safely inferred from code or configuration:

```json
{
  "artifacts": [
    {
      "id": "payments-api",
      "path": "openapi.yaml",
      "role": "source",
      "source_of_truth": true
    }
  ],
  "dependencies": [
    {"target": "workspace.payment-service", "kind": "runtime_api"}
  ],
  "source_of_truth": [
    {
      "domain": "payments.api",
      "authority": {"project_id": "workspace.payment-service", "artifact": "payments-api"}
    }
  ]
}
```

The v0.3 manifest is JSON to keep the core dependency-free. YAML adapters are
planned for a future release.

Preflight is read-only and reports matched projects, affected projects,
source-of-truth rules, suggested validation commands, and unresolved changes:

```bash
aine preflight --root /path/to/workspace --change payment-service/openapi.yaml
```

Git-aware preflight can read the working tree, index, or a base comparison:

```bash
aine preflight --root /path/to/workspace --diff
aine preflight --root /path/to/workspace --staged
aine preflight --root /path/to/workspace --base origin/main --format markdown
```

Artifact metadata may declare `risk` (`low`, `medium`, `high`, or `critical`)
and `approval_required`. High and critical artifacts produce human-review
signals; AINE does not block Git or deployment operations.

Projects may also declare advisory policy in `.aine/registry.json`:

```json
{
  "project": {
    "policy": {
      "require_approval_for": ["high", "critical"],
      "deny_unknown_changes": true,
      "required_checks": ["test", "lint"]
    }
  }
}
```

Policy results are included in preflight, evidence, and handoff records. They
can be `pass`, `review_required`, or `fail`, but remain advisory-only: the
public core does not block edits, commits, CI, or deployment.

Save a machine-readable evidence bundle and create a handoff record:

```bash
aine preflight --root /path/to/workspace --diff --output preflight.json
aine handoff --preflight preflight.json --format markdown
```

Evidence and handoff records are deterministic, read-only artifacts for review
and agent coordination. They do not approve, merge, deploy, or mutate projects.

## GitHub Actions integration

The repository includes a reference workflow at
`.github/workflows/aine-preflight.yml`. Copy it into a project or portfolio
repository to run read-only preflight on pull requests. It compares the
checkout with `origin/main` and uploads JSON evidence plus a Markdown report as
the `aine-preflight` workflow artifact.

The workflow assumes the checked-out repository is the registry boundary. For
multi-root portfolios, pass additional `--root` values in the workflow and
ensure those roots are available in the runner workspace.

## Contract adapters

The built-in OpenAPI adapter recognizes `api.*`, `openapi.*`, and `swagger.*`
JSON/YAML files when they contain an OpenAPI version declaration. It registers
them as `schema` artifacts with `kind: openapi_contract`, including the format
and version, so contract changes participate in preflight and impact analysis.
The adapter is deliberately syntax-light and does not validate the complete
OpenAPI document; use a dedicated validator in project CI for that purpose.

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
