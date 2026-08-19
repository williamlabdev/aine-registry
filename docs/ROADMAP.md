# AINE Registry Roadmap

This roadmap describes product outcomes, not promises of automatic support for every language or runtime.

## v0.1 — Public Registry Core (released)

- [x] Discover multiple workspace roots
- [x] Identify Git projects, repositories, and checkouts
- [x] Register selected source and generated artifacts
- [x] Infer evidence-backed package, filesystem, and runtime references
- [x] Classify dependency scope, including cross-root relationships
- [x] Export portable snapshots with local-path redaction
- [x] Query dependency graphs and transitive impact
- [x] Provide a versioned JSON Schema
- [x] Keep discovery read-only and dependency-free

## v0.2 — Developer Workflow (released)

- [x] Stable `aine` executable wrapper
- [x] `scan`, `impact`, and `validate` command forms
- [x] Installable package metadata with no runtime dependencies
- [x] Human-readable impact reports
- [x] Better diagnostics for unresolved and contradictory relationships
- [ ] Public examples for a small polyrepo system

## v0.3 — Agent Preflight (released)

- [x] Project-local `.aine/registry.json` manifest for explicit metadata
- [x] Generic source-of-truth declarations
- [x] Artifact provenance and consumer declarations
- [x] `preflight` command for a proposed file or change set
- [x] Affected-project report with project boundaries
- [x] Required validation plan derived from project commands
- [x] Unknown and unresolved change reporting
- [x] Risk annotations and human-review requirements

## v0.4 — Change Assurance (released)

- [x] Git working-tree, staged, and base comparison change discovery
- [x] Artifact and project risk metadata
- [x] Human-review and approval-required signals
- [x] JSON and Markdown preflight reports
- [x] Handoff and evidence record formats

## v0.5 — Evidence and Handoff (released)

- [x] `aine preflight --output` evidence bundle
- [x] Stable `aine.evidence.v1` record
- [x] `aine handoff --preflight` handoff record
- [x] Stable `aine.handoff.v1` record
- [x] Human-review status and next actions

## v0.6 — Advisory Policy (released)

- [x] Project-local policy declarations
- [x] Approval thresholds by risk level
- [x] Unknown-change and required-check policy rules
- [x] Policy results in preflight, evidence, and handoff records
- [x] Advisory-only behavior with no mutation or blocking

## v0.7 — Integrations (released)

- [x] GitHub Actions reference workflow for PR preflight artifacts
- [x] Lightweight OpenAPI contract adapter
- [x] Lightweight Protobuf contract adapter
- [x] Lightweight AsyncAPI contract adapter
- [x] Lightweight Docker, Helm, and Kubernetes deployment adapter
- [x] GitHub Actions workflow provenance adapter

## v0.8 — Explicit Relationship Provider (released)

- [x] Manifest `relationships` declarations
- [x] Typed relationship kind, status, strength, and evidence
- [x] Service/event relationship edges represented in the existing graph

## v0.9 — Static Module Import Discovery (released)

- [x] Python import discovery
- [x] JavaScript and TypeScript import discovery
- [x] Go import discovery
- [x] Rust import discovery
- [x] Portable import records with local/external resolution and evidence
- [x] Project dependency projection for external and workspace imports

## v0.10 — Authorization and Policy Context (released)

- [x] Opt-in enforced policy evaluation with machine-readable exit status
- [x] Portable authorization context with RBAC roles and ABAC conditions

## v0.11 — Approval Request Boundary (released)

- [x] Generate read-only approval requests from preflight evidence
- [x] Include approval requests in handoff records
- [x] Provide `aine approval --handoff` export
- [x] Record external approval decisions without executing them

## v1.0 — Stable Governance Core (released)

- [x] Team ownership metadata with portable owner/delegate relationships
- [x] Relationship-aware authorization for owners and delegated teams
- [x] Evidence output directly consumable as `aine.evidence.v1`
- [x] Collision-safe multi-root identities
- [x] Explicit unresolved dynamic import evidence
- [x] Local Go module import resolution and import alias normalization

## v1.1 — Local Audit Evidence Store (released)

- [x] Content-addressed local evidence records
- [x] Append-only file creation with integrity verification
- [x] `aine evidence store`, `list`, and `get` commands
- [x] Tamper detection without runtime dependencies

## Later — Control Plane

- [x] Static self-hostable portfolio view export
- [ ] Hosted portfolio service
- [ ] Enterprise retention and compliance integrations

## Release principles

1. Do not hide uncertainty to improve graph completeness metrics.
2. Do not add a hosted dependency to the local core without a clear adapter boundary.
3. Prefer evidence and explainability over speculative semantic inference.
4. Every new relationship type needs fixtures and regression tests.
5. Keep business-specific metadata out of the public core.
