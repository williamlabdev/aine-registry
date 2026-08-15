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
- [ ] Better diagnostics for unresolved and contradictory relationships
- [ ] Public examples for a small polyrepo system

## v0.3 — Agent Preflight (released)

- [x] Project-local `.aine/registry.json` manifest for explicit metadata
- [x] Generic source-of-truth declarations
- [x] Artifact provenance and consumer declarations
- [x] `preflight` command for a proposed file or change set
- [x] Affected-project report with project boundaries
- [x] Required validation plan derived from project commands
- [x] Unknown and unresolved change reporting
- [ ] Risk annotations and human-review requirements

## v0.4 — Change Assurance (released)

- [x] Git working-tree, staged, and base comparison change discovery
- [x] Artifact and project risk metadata
- [x] Human-review and approval-required signals
- [x] JSON and Markdown preflight reports
- [ ] Handoff and evidence record formats

## v0.5 — Evidence and Handoff (released)

- [x] `aine preflight --output` evidence bundle
- [x] Stable `aine.evidence.v1` record
- [x] `aine handoff --preflight` handoff record
- [x] Stable `aine.handoff.v1` record
- [x] Human-review status and next actions

## v0.6 — Integrations

- [ ] GitHub Actions integration
- [ ] OpenAPI, protobuf, and AsyncAPI adapters
- [ ] Docker, Helm, and Kubernetes deployment adapters
- [ ] CI test and artifact provenance adapters
- [ ] Pluggable service and event relationship providers

## Later — Control Plane

- [ ] Policy evaluation and approval workflows
- [ ] Team ownership and RBAC
- [ ] Audit evidence storage
- [ ] Hosted or self-hosted portfolio views
- [ ] Enterprise retention and compliance integrations

## Release principles

1. Do not hide uncertainty to improve graph completeness metrics.
2. Do not add a hosted dependency to the local core without a clear adapter boundary.
3. Prefer evidence and explainability over speculative semantic inference.
4. Every new relationship type needs fixtures and regression tests.
5. Keep business-specific metadata out of the public core.
