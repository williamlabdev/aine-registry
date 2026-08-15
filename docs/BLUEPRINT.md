# AINE Registry Blueprint

**Status:** APPROVED FOR PUBLIC CORE v0.3
**Scope:** local-first registry and impact analysis  
**Related:** [Vision](VISION.md), [Roadmap](ROADMAP.md)

## Overview

AINE Registry is a read-only Python CLI that scans one or more workspace roots, identifies independent Git projects, extracts selected metadata and artifacts, resolves evidence-backed relationships, and emits a deterministic portable snapshot. Queries operate on the snapshot or perform discovery directly against explicitly supplied roots.

## Boundary

```mermaid
flowchart LR
    W[Workspace roots] --> D[Discovery engine]
    D --> N[Normalized registry model]
    N --> S[Portable snapshot]
    S --> Q[Graph and impact queries]
    Q --> F[Preflight report]
    F --> C[Agent context / human report]
    M[.aine/registry.json metadata] --> N
    P[Future policy layer] -. consumes .-> S
```

The core reads local files and Git metadata. It does not edit discovered projects, install dependencies, run generators, execute agents, deploy, or mutate a database.

Project-local `.aine/registry.json` metadata is explicit input, not executable
configuration. The core uses it to declare artifacts, dependencies, and
source-of-truth relationships that static discovery cannot reliably infer.

## Core objects

| Object | Responsibility |
| :--- | :--- |
| Portfolio | Groups one or more workspace roots under a logical registry scope. |
| Workspace root | Defines a local trust and Git discovery boundary. |
| Project | Buildable, runtime, or explicitly registered Git boundary. |
| Repository | Canonical remote identity, separated from local checkout identity. |
| Checkout | Root-qualified local instance with branch, commit, and dirty state. |
| Artifact | Source, generated, projection, schema, evidence, config, or deployment output. |
| Dependency | Directed typed edge with scope, strength, and evidence. |
| Source of truth | Explicit authority and projection relationship. |
| Finding | Uncertainty, unresolved provider, exclusion, or other explainable observation. |

## Discovery flow

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant Scanner
    participant Git
    participant Snapshot

    User->>CLI: supply one or more roots
    CLI->>Scanner: discover files and project boundaries
    Scanner->>Git: read remote, branch, commit, dirty state
    Scanner->>Scanner: infer artifacts and typed edges
    Scanner->>Snapshot: normalize and redact local paths
    Snapshot-->>CLI: JSON snapshot or query result
    CLI-->>User: graph, impact, preflight, or validation output
```

## Identity and portability

- Local absolute paths are resolution inputs only.
- Portable output uses root-qualified relative paths.
- Repository identity is based on normalized remote identity when available.
- Checkout identity remains distinct so duplicate local checkouts are visible.
- Snapshot hashes exclude observation time and machine-local path identity.

## Evidence and uncertainty

Every inferred dependency should retain an evidence path or reference. The model distinguishes `active`, `historical`, `planned`, and `unknown` relationships. An unresolved or dynamic relationship is represented as `UNKNOWN`; it is not treated as proof that no relationship exists.

## Preflight behavior

`preflight` accepts one or more changed paths and produces a read-only report:

1. Match changes to registered projects and artifacts.
2. Traverse incoming dependency edges to identify affected projects.
3. Select source-of-truth rules related to the change or affected boundary.
4. Collect declared project validation commands (`test`, `verify`, `lint`, `build`).
5. Report unmatched changes and registry findings as human-review signals.

Git-aware modes derive changes from each discovered checkout:

- `--diff` reads staged and unstaged changes relative to `HEAD`.
- `--staged` reads the Git index.
- `--base REF` compares `REF...HEAD`.

Risk is advisory in the public core. High or critical artifact risk, or an
explicit `approval_required`, creates a human-review signal; it does not block
the filesystem, Git, CI, or deployment.

## Extension boundaries

The public core should remain generic. Project-specific business metadata, source-of-truth rules, service catalogs, deployment providers, policy engines, agent execution, and hosted storage belong in adapters or higher-level control-plane components.

## Verification gates

Changes to the core must preserve:

- deterministic portable snapshots
- no absolute path leakage in normal output
- no credential export from Git remotes
- read-only discovery behavior
- regression coverage for multi-root and cross-root impact
