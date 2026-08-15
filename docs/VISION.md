# AINE Registry Vision

## Vision statement

AINE Registry gives humans and AI coding agents a trustworthy, portable map of distributed software before a change is made.

The registry connects projects, repositories, checkouts, artifacts, dependencies, and sources of truth so that an agent can answer:

```text
What am I allowed to change?
What else could this change affect?
Which artifact is authoritative?
What evidence supports that conclusion?
What is still unknown and needs human review?
```

## Problem

Modern engineering work is spread across multiple repositories, workspace roots, generated artifacts, services, deployment manifests, and teams. AI agents make changes quickly, but they do not automatically know the system boundary or the difference between a source artifact and a derived projection.

The result can be a correct-looking change in the wrong repository, a stale generated file, an incomplete cross-project change, or a validation plan that misses a downstream consumer.

## Mission

AINE Registry provides the smallest reliable context layer between an AI agent and a distributed software portfolio.

It is designed to be:

- **Local-first** — source code and workspace paths stay under the user's control.
- **Evidence-based** — relationships include how they were discovered.
- **Portable** — snapshots do not depend on one machine's absolute paths.
- **Read-only by default** — discovery does not mutate projects or execute deployments.
- **Explicit about uncertainty** — `UNKNOWN` is a useful result, not a failure to hide.
- **Composable** — project-specific metadata and future control-plane policies live in adapters.

## Non-goals

AINE Registry is not currently:

- an AI coding agent
- a replacement for CI/CD, service catalogs, or observability systems
- a runtime service mesh or deployment controller
- an automatic interpreter of business rules
- a guarantee that dynamic runtime relationships are completely discoverable

## Long-term direction

The registry is the foundation for an evidence-based change assurance layer:

```text
Discover → Explain impact → Check policy → Validate → Record evidence
```

The core remains open and local-first. Higher-level governance, approvals, hosted collaboration, and enterprise integrations can be built around it without making the registry depend on a hosted control plane.
