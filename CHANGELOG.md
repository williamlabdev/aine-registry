# Changelog

All notable changes to AINE Registry are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **License change:** releases up to and including 1.4.3 were distributed under
> the MIT License. From 1.5.0 the project is distributed under Apache-2.0. See
> [NOTICE](NOTICE) and the 1.5.0 entry below.

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
