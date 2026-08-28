# Changelog

All notable changes to AINE Registry are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **License change:** releases up to and including 1.4.3 were distributed under
> the MIT License. From 1.5.0 the project is distributed under Apache-2.0. See
> [NOTICE](NOTICE) and the 1.5.0 entry below.

## [Unreleased]

### Added
- The `REL-003` finding: a project whose `manifest.yaml` still declares a
  top-level `depends_on:` block is reported, because nothing reads or resolves
  that block and the edges in it drift from the registry manifest silently.

- `published_projects` in the local configuration, and the `PRJ-002` finding it
  enables: a published project whose manifest names an unpublished project is
  reported so the edge can be moved to a relationship overlay.

- `project.id` in a project manifest is now honored when resolving references.
  The field was part of the v1 manifest schema but was read nowhere, so a
  manifest that named its peers by their declared identifiers produced only
  external phantom edges whenever the configured root topology derived a
  different ID. The declared value is an alias used for relationship targets,
  overlay owners, and `impact` queries, and is reported on the project as
  `declared_id`; the derived `project_id` remains the project's identity. A
  declared ID claimed by two projects, or taking an identifier another project
  is already found by, is reported as finding `PRJ-001` and left unhonored.
- `relationship_overlays` in the local configuration declares relationships
  outside the project repositories. A manifest travels with its project, so a
  target named there is published wherever the project is published; an edge
  into a private project could not be declared without publishing that
  project's identifier. Overlay edges are merged at discovery time and behave
  identically in queries, context bundles, and impact analysis, carrying
  `relationship_source: "overlay"` and `<local-overlay>` evidence instead of a
  manifest path. An overlay naming an undiscovered project is ignored rather
  than recorded as an external edge.

## [1.7.0] - 2026-08-27

### Added

- `evidence list --correlation` and `evidence export --correlation` scope a
  listing or an audit bundle to one correlation identifier, so a single
  producer run can be reviewed without reading the whole store. A scoped bundle
  declares its `correlation_id`; an unscoped one claims no scope.
- Every audit bundle now reports `unresolved_refs`: references the bundle does
  not carry, marked `out_of_scope` when the store holds the record and the
  bundle excluded it, and `missing` when the store cannot produce it at all.
  A snapshot belongs to no single correlation, so scoping a bundle to one run
  necessarily leaves its snapshot out; the bundle names what is absent instead
  of widening its own scope or leaving a join that cannot be followed.
- `/api/evidence?correlation=<id>` answers the same scoped question as the CLI.
- Regression fixtures for scoped listings and bundles, an unresolvable
  reference, an unreadable record staying visible in a scoped listing, and the
  read-only API route.

Correlation identifiers became visible in 1.6.0 but could not be queried; a
reviewer had to list the whole store and filter by hand.

Only a field the contract defines as a stored record's identity is followed.
`aine.registry.v1` also carries a `snapshot_id`, but that value is the digest
of the snapshot's own canonical content rather than the identity of a stored
envelope, so it is deliberately not chased and no snapshot is reported as a
dangling reference to itself.

## [1.6.0] - 2026-08-27

### Added

- The local evidence store accepts
  `aine.control-plane.integration-observation.v1`. An observation is the
  adapter boundary for a separate enforcement or evaluation product: it carries
  that product's run identifier, a shared correlation identifier, the
  `record_id` of an already-stored snapshot, and the digest of the native
  result, so a producer run and the portfolio state it ran against become one
  correlated set. The native payload, credentials, and machine-local paths stay
  out of the record, and the core takes no dependency on the producing product.
- `evidence list` reports a record's correlation identifier when it has one.
- Regression fixtures for the snapshot join, the audit bundle, and the
  unchanged rejection of unsupported record schemas.

Demonstrated end to end against a real Orvena benchmark run: a portfolio
snapshot stored as `aine.registry.v1`, an observation built from that run and
referencing the stored snapshot digest, both exported in one
`aine.audit.bundle.v1` with the join resolving and no native payload present.

## [1.5.0] - 2026-08-27

### Changed

- **Relicensed from MIT to Apache-2.0** by the sole copyright holder, with a
  `NOTICE` file recording the provenance. Copies distributed before 2026-08-27
  remain available under the MIT terms they were received under.
- Non-relative Python imports now also resolve against the importing file's
  directory. A module written as the `from .module import x` /
  `from module import x` fallback pair used by scripts resolved only in the
  relative spelling; the bare spelling fell through to `external:<module>`, so
  a project was reported as depending on itself as an external package and each
  internal dependency was counted twice. Project-root and `src/` candidates
  still take precedence.
- Standard-library imports are recorded as resolved dependencies on the
  language runtime rather than unknown external providers. They carry
  `resolution: "stdlib"` and a `stdlib:<language>:<module>` target, remain
  visible as import records, and produce no project dependency edge and no
  `DEP-001` finding. Recognition is exact for Python, Node, and Rust, and
  follows the domain-element rule for Go.
- Workflow actions moved onto the Node 24 runtimes, clearing the Node 20
  deprecation warnings.

### Added

- `stdlib` in the `resolution` enum of `registry.v1.schema.json`.
- Regression fixtures for sibling fallback imports, standard-library imports in
  all four supported languages, and prose rejection.

### Fixed

- The textual Python import pattern matched documentation lines beginning with
  `import `, recording whole sentences as module names. Captured values must
  now be dotted identifier paths.
- The OSV reusable workflow failed at startup because the calling job granted
  fewer permissions than the called workflow declares.

Scanning this repository before the change reported 91 external providers and
33 unknown-dependency findings for a project with no third-party runtime
dependency. It now reports 117 import records — 67 standard library, 50 local,
zero external, zero unresolved.

## [1.4.3] - 2026-08-27

### Changed

- The installed console script is now `aine-registry`, matching the package and
  distribution name.
- Portfolio, governance, and integration topology edges no longer expand
  change-impact traversal unless the manifest sets `impact: true`. Topology and
  change impact are different questions and were being answered as one.

### Added

- Portable validation commands declared in project manifests (`project.commands`).
- Secret scanning and OSV dependency scanning workflows.
- A project manifest for this repository, and build artifact ignores.

## [1.4.2] - 2026-08-20

### Changed

- Split the registry monolith into discovery, analysis, evidence, view, CLI,
  shared, and constants modules, preserving the `registry.aine_registry`
  facade, the CLI entry point, the snapshot schema, and read-only behavior.

### Added

- A module-boundary regression gate.

## [1.4.1] - 2026-08-20

### Fixed

- Hardened the verification gates and packaging metadata.

## [1.4.0] - 2026-08-19

### Added

- A public polyrepo example workspace, with its manifests included in the
  distributed package.

## [1.3.0] - 2026-08-19

### Added

- A stdlib-only self-hosted portfolio server with read-only snapshot and
  evidence API routes.
- Portable audit bundle export and a review-only retention manifest.

## [1.2.0] - 2026-08-19

### Added

- Static portfolio view export.

## [1.1.0] - 2026-08-19

### Added

- A content-addressed, append-only local evidence store with integrity
  verification, and the `evidence store`, `list`, and `get` commands.

## [1.0.0] - 2026-08-19

First stable governance core.

### Added

- Team ownership metadata with portable owner and delegate relationships.
- Relationship-aware authorization for owners and delegated teams.
- Evidence output directly consumable as `aine.evidence.v1`.
- Collision-safe multi-root identities.
- Explicit unresolved dynamic import evidence.
- Local Go module import resolution and import alias normalization.

## [0.11.0] - 2026-08-19

### Added

- Read-only approval request records, and `approval --handoff` export.

## [0.10.0] - 2026-08-19

### Added

- Portable authorization context with RBAC roles and ABAC conditions.

## [0.9.1] - 2026-08-19

### Fixed

- Preflight evidence is preserved when policy evaluation fails.

## [0.9.0] - 2026-08-19

### Added

- Static module import discovery for Python, JavaScript, TypeScript, Go, and
  Rust, with portable import records and project dependency projection.

## [0.8.6] - 2026-08-19

### Added

- Opt-in enforced policy mode with machine-readable exit status.

## [0.8.5] - 2026-08-19

### Added

- Relationship conflict diagnostics.

## [0.8.4] - 2026-08-15

### Added

- Snapshot structural validation.

## [0.8.3] - 2026-08-15

### Added

- Scoped agent context command.

## [0.8.2] - 2026-08-15

### Added

- Relationship query filters.

## [0.8.1] - 2026-08-15

### Added

- A first-class relationships view.

## [0.8.0] - 2026-08-15

### Added

- Explicit relationship declarations in manifests, with typed kind, status,
  strength, and evidence.

## [0.7.5] - 2026-08-15

### Added

- CI workflow provenance adapter.

## [0.7.4] - 2026-08-15

### Added

- Docker, Helm, and Kubernetes deployment descriptor adapter.

## [0.7.3] - 2026-08-15

### Added

- AsyncAPI contract adapter.

## [0.7.2] - 2026-08-15

### Added

- Protobuf contract adapter.

## [0.7.1] - 2026-08-15

### Added

- OpenAPI contract adapter.

## [0.7.0] - 2026-08-15

### Added

- GitHub Actions reference workflow for pull-request preflight artifacts.

## [0.6.0] - 2026-08-15

### Added

- Advisory policy evaluation: project-local policy declarations, approval
  thresholds by risk level, and policy results in preflight, evidence, and
  handoff records. Advisory only — no mutation, no blocking.

## [0.5.0] - 2026-08-15

### Added

- Evidence bundles and handoff records: `aine.evidence.v1` and
  `aine.handoff.v1`.

## [0.4.0] - 2026-08-15

### Added

- Git-aware change assurance: working-tree, staged, and base comparison change
  discovery, with JSON and Markdown preflight reports.

## [0.3.0] - 2026-08-15

### Added

- Project-local `.aine/registry.json` manifests and the `preflight` command.

## [0.2.0] - 2026-08-15

### Added

- An installable CLI with `scan`, `impact`, and `validate` command forms, and
  package metadata with no runtime dependencies.

## [0.1.1] - 2026-08-15

### Added

- Vision, roadmap, and blueprint documents; CLI examples aligned with v0.1
  behavior.

## [0.1.0] - 2026-08-15

Initial public release.

### Added

- Multi-root workspace discovery of Git projects, repositories, and checkouts.
- Evidence-backed package, filesystem, and runtime reference inference.
- Dependency scope classification, including cross-root relationships.
- Portable snapshot export with local-path redaction.
- Dependency graph and transitive impact queries.
- A versioned JSON Schema, with discovery kept read-only and dependency-free.
