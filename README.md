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

## Security checks

Install the external scanners and run the repository gate before review:

```bash
brew install gitleaks osv-scanner
./tools/security_scan.sh
```

The gate scans Git history and the working tree for secrets and recursively
checks dependency manifests with OSV-Scanner. The public core has no Node
package manifest, so `npm audit` is not applicable here.

To install the `aine-registry` command in the optional virtual environment:

```bash
python3 -m pip install -e .
aine-registry --help
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
aine-registry scan --root /path/to/workspace
aine-registry impact --root /path/to/workspace --project workspace.app
aine-registry validate --root /path/to/workspace
aine-registry preflight --root /path/to/workspace --change app/api.yaml
aine-registry preflight --root /path/to/workspace --diff --format markdown
aine-registry preflight --root /path/to/workspace --staged
aine-registry preflight --root /path/to/workspace --base origin/main
```

The installed `aine-registry` command is the supported interface. The Python
script entrypoint remains available for backwards compatibility. The standalone
`aine` command belongs to Orvena's deprecated compatibility alias and is not
installed by this package.

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

The optional `project.commands` object declares portable validation commands
(`test`, `verify`, `lint`, and `build`) for preflight. Commands are recorded as
evidence and surfaced in the report; the Registry never executes them. A string
value is accepted for compact manifests, or an object may include an explicit
`command` and `evidence` path.

For typed service or event relationships that static discovery cannot infer,
the manifest also accepts a `relationships` array. Each entry names a target
and may declare `relationship_type`, `status`, and `strength`; the resulting
edge retains the manifest as evidence.

Query explicit relationships directly with:

```bash
aine-registry relationships --root /path/to/workspace
aine-registry relationships --root /path/to/workspace --relationship-type event_consumer
aine-registry relationships --root /path/to/workspace --relationship-status planned
```

Snapshots expose the same records under `relationships`; the original
`dependencies` collection remains available for compatibility.

Build a scoped agent context bundle with:

```bash
aine-registry context --root /path/to/workspace --project checkout-service
```

The result includes the selected project, its artifacts, touching dependencies
and relationships, related source-of-truth rules, findings, and snapshot ID.

Validate a snapshot before handing it to an agent or CI step:

```bash
aine-registry validate --root /path/to/workspace
aine-registry validate --snapshot /tmp/aine-portfolio.json
```

Validation checks the registry schema identity, core collection shape, edge
endpoints/scopes, required identities, and absolute-path redaction.

Discovery findings also identify unresolved providers (`DEP-001`), conflicting
source-of-truth authorities (`SOT-001`), and contradictory dependency status
or strength declarations (`REL-002`). AINE reports these conditions for human
review rather than choosing an authority automatically.

Preflight is read-only and reports matched projects, affected projects,
source-of-truth rules, suggested validation commands, and unresolved changes:

```bash
aine-registry preflight --root /path/to/workspace --change payment-service/openapi.yaml
```

Git-aware preflight can read the working tree, index, or a base comparison:

```bash
aine-registry preflight --root /path/to/workspace --diff
aine-registry preflight --root /path/to/workspace --staged
aine-registry preflight --root /path/to/workspace --base origin/main --format markdown
```

Artifact metadata may declare `risk` (`low`, `medium`, `high`, or `critical`)
and `approval_required`. High and critical artifacts produce human-review
signals; AINE does not block Git or deployment operations.

Projects may also declare advisory policy in `.aine/registry.json`:

```json
{
  "project": {
    "policy": {
      "mode": "advisory",
      "require_approval_for": ["high", "critical"],
      "deny_unknown_changes": true,
      "required_checks": ["test", "lint"]
    }
  }
}
```

Policy results are included in preflight, evidence, and handoff records. They
can be `pass`, `review_required`, or `fail`. The default `advisory` mode always
returns exit code 0. Opt into machine-readable enforcement for a run with:

```bash
aine-registry preflight --root /path/to/workspace --diff --policy-mode enforced
```

In `enforced` mode, a non-`pass` policy returns exit code 1 and sets
`policy.enforced_failure: true`; it still does not modify Git, files, CI, or
deployment. A CLI mode override takes precedence over the project manifest.

Policy authorization can also combine RBAC roles with ABAC conditions:

```json
{
  "project": {
    "policy": {
      "authorization": {
        "rules": [
          {
            "id": "platform-preflight",
            "effect": "allow",
            "actions": ["preflight"],
            "roles": ["developer"],
            "conditions": {
              "subject.attributes.team": "platform",
              "resource.risk": "low"
            }
          }
        ]
      }
    }
  }
}
```

The caller supplies a portable subject context with `--subject-id`, repeated
`--role`, and `--attribute key=value` options. AINE evaluates roles and
attributes, then records `allow`, `review_required`, or `fail` decisions. It
does not provide an identity provider or perform approval, merge, or deployment
actions.

Save a machine-readable evidence bundle and create a handoff record:

```bash
aine-registry preflight --root /path/to/workspace --diff --output preflight.json
aine-registry handoff --preflight preflight.json --format markdown
```

Evidence and handoff records are deterministic, read-only artifacts for review
and agent coordination. They do not approve, merge, deploy, or mutate projects.

When review or approval is required, the handoff includes an
`aine.approval.v1` request. It can also be emitted independently:

```bash
aine-registry approval --handoff handoff.json --output approval.json
```

The request records `requested`, `blocked`, or `not_required` state and keeps
the evidence ID, reasons, and affected projects. An external decision can be
recorded without executing it:

```bash
aine-registry approval --handoff handoff.json \
  --decision approved --decided-by human.william \
  --output approval.json
```

AINE records the supplied decision as external input; it does not contact an
identity, ticketing, merge, or deployment system.

Evidence can be retained in a local, append-only, content-addressed store:

```bash
aine-registry evidence store --input preflight.json --store .aine/evidence
aine-registry evidence list --store .aine/evidence
aine-registry evidence get --id sha256:<digest> --store .aine/evidence
```

The store verifies each record's digest on read and reports tampered records as
invalid. It has no database or hosted-service dependency and only writes to the
explicit store directory supplied by the caller.

Portable snapshots can be rendered as a static self-hostable portfolio view:

```bash
aine-registry view --snapshot registry.json --output portfolio.html
```

The HTML output is dependency-free and contains only portable project metadata;
serve it from any static host when a hosted view is desired.

For a local self-hosted view with read-only JSON routes:

```bash
aine-registry serve --snapshot registry.json --store .aine/evidence --host 127.0.0.1 --port 8080
```

The server exposes `/`, `/api/snapshot`, and `/api/evidence`. It uses only the
Python standard library and binds to loopback by default.

Audit bundles and retention manifests are portable exports:

```bash
aine-registry evidence export --store .aine/evidence --output audit-bundle.json
aine-registry evidence retention --store .aine/evidence --retain-days 365 \
  --as-of 2026-08-19T00:00:00+00:00 --output retention.json
```

Retention is deliberately review-only: AINE never deletes records or claims
compliance. External archive, SIEM, GRC, and enterprise retention adapters can
consume these exports.

Projects can declare portable ownership and delegated teams:

```json
{
  "project": {
    "ownership": {
      "team": "platform",
      "owners": ["team:platform"],
      "delegates": ["release"]
    }
  }
}
```

Authorization rules may use `teams` or `requires_ownership`. The caller can
provide `--team` and `--delegated-by` during preflight. These are evidence
inputs for policy evaluation, not identity verification.

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

Protobuf `.proto` files are likewise registered as `schema` artifacts with
`kind: protobuf_contract`. AINE records syntax, package, and declared service
names without invoking `protoc` or inferring wire compatibility.

AsyncAPI JSON/YAML files named `asyncapi.*` or `events.*` are registered as
`asyncapi_contract` schema artifacts when an AsyncAPI version and `channels`
section are present. The adapter records version and a lightweight channel
count; event semantics and broker validation remain outside the registry core.

Deployment descriptors are also inventoried: Dockerfiles, Compose files,
Helm `Chart.yaml`, and common Kubernetes manifests become `deployment`
artifacts with a deployment format/kind. AINE does not execute Docker, Helm,
or kubectl and does not infer that a deployment succeeded.

GitHub Actions workflows under `.github/workflows/` are registered as
`provenance` artifacts with their workflow jobs. This exposes CI definitions
to impact analysis without executing workflows, accessing secrets, or claiming
that a check passed.

## Module imports

Static module imports are exposed through the portable `imports` collection and
the CLI:

```bash
aine-registry imports --root /path/to/workspace
```

The built-in adapter supports Python, JavaScript, TypeScript, Go, and Rust. It
records source path, language, specifier, static/dynamic kind, resolution,
target path when resolvable, and evidence.

Resolution is one of `local`, `stdlib`, `workspace_package`, `external`, or
`unresolved`. Local and standard-library imports remain file-level records;
external and workspace-package imports also become project dependency edges.
Unresolvable imports remain explicit rather than being treated as absent.

A standard-library import is a resolved dependency on the language runtime, not
an unknown provider, so it carries a `stdlib:<language>:<module>` target and
produces no project dependency edge. A Python module that is imported both
relatively and by its bare name — the `from .module import x` /
`from module import x` fallback pair used by scripts — resolves to the same
sibling file in both spellings, so one dependency is not counted twice.

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

Run all repository verification gates, including the public polyrepo fixture:

```bash
python3 tools/verify.py
```

The CLI also exposes its package version without requiring an editable install:

```bash
python3 registry/aine_registry.py --version
```

## Project documents

- [Vision](docs/VISION.md) — why the registry exists and what it will not do
- [Roadmap](docs/ROADMAP.md) — released capabilities and planned milestones
- [Public polyrepo example](examples/polyrepo/README.md) — runnable multi-root fixture with cross-project relationships
- [Blueprint](docs/BLUEPRINT.md) — public-core boundaries, data model, and runtime flow

## Public-core boundary

This repository contains generic discovery and graph behavior only. Portfolio-specific project IDs, business metadata, source-of-truth seeds, customer data, local snapshots, and private evidence belong in a project adapter or private workspace configuration.

Absolute local paths are used only during discovery and are redacted from portable snapshots and normal output.

## Status

Experimental public core. Relationship inference is evidence-based and incomplete for dynamic runtime behavior. Unknown relationships remain `UNKNOWN`; they are not silently treated as absent.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Relicensed from MIT on 2026-08-27 by the sole copyright holder; copies
distributed before that date remain available under their original MIT terms.
