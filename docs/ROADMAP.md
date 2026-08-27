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

- [x] Stable `aine-registry` executable wrapper
- [x] `scan`, `impact`, and `validate` command forms
- [x] Installable package metadata with no runtime dependencies
- [x] Human-readable impact reports
- [x] Better diagnostics for unresolved and contradictory relationships
- [x] Public examples for a small polyrepo system

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

- [x] `aine-registry preflight --output` evidence bundle
- [x] Stable `aine.evidence.v1` record
- [x] `aine-registry handoff --preflight` handoff record
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
- [x] Provide `aine-registry approval --handoff` export
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
- [x] `aine-registry evidence store`, `list`, and `get` commands
- [x] Tamper detection without runtime dependencies

## v1.3 — Self-hosted Service and Compliance Export (released)

- [x] Stdlib-only self-hosted portfolio server
- [x] Read-only snapshot and evidence API routes
- [x] Portable audit bundle export
- [x] Portable retention manifest with review-only semantics

## v1.4.3 — Portfolio baseline alignment (released)

- [x] Rename the installed console script to `aine-registry`, matching the
  package and distribution name
- [x] Declare portable validation commands in project manifests
- [x] Keep portfolio/governance/integration topology edges separate from
  change-impact traversal unless explicitly opted in
- [x] Establish manifests for the AINE and Control Plane portfolio roots
- [x] Preserve the read-only boundary while producing a seven-project snapshot
- [x] Add real Orvena and airt evidence adapters after a shared run/correlation
  contract is demonstrated end to end. Demonstrated with a real Orvena benchmark
  run and three real airt native-hook runs sharing one correlation identifier,
  each joined to a stored snapshot through the local evidence store. The
  producer-side adapters live outside this repository, and airt does not itself
  declare the native schema identifier the observation records for it

## v1.5 — Import fidelity (released)

- [x] Resolve Python script-mode fallback imports to the sibling module they
  actually load, so a project stops depending on itself as an external package
- [x] Recognize Python, Node, Go, and Rust standard-library providers as
  resolved imports with no project dependency edge
- [x] Reject prose that begins with `import` instead of recording it as a module
- [x] Add `stdlib` to the portable import resolution contract
- [x] Regression fixtures for sibling fallback imports and standard-library
  imports in every supported language

## v1.6 — Correlated producer evidence (released)

- [x] Accept `aine.control-plane.integration-observation.v1` in the local
  evidence store, so a separate product's run joins a stored snapshot
- [x] Expose correlation identifiers in the evidence index
- [x] Keep native payloads, credentials, and machine-local paths out of the
  stored record, and keep the producing product out of the core's dependencies
- [x] Regression fixtures for the snapshot join, the audit bundle, and the
  unchanged rejection of unsupported record schemas

## Later — Control Plane

- [ ] Managed hosted portfolio service
- [ ] Provider-specific enterprise retention and compliance adapters

## Release principles

1. Do not hide uncertainty to improve graph completeness metrics.
2. Do not add a hosted dependency to the local core without a clear adapter boundary.
3. Prefer evidence and explainability over speculative semantic inference.
4. Every new relationship type needs fixtures and regression tests.
5. Keep business-specific metadata out of the public core.
