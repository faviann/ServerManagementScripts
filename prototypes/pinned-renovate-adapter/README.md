# PROTOTYPE — pinned Renovate dry-run adapter

## Question

Can a pinned, read-only Renovate local dry run consume disposable effective-Compose projections and expose enough validated facts to normalize stack-scoped image update observations, including isolated lookup failures, without changing the repository or creating branches or pull requests?

This is throwaway decision code for [Prototype the pinned Renovate dry-run adapter](https://github.com/faviann/homelab-iac/issues/90). It is not an implementation of the eventual stack update skill.

## Run

```bash
uv run --locked python prototypes/pinned-renovate-adapter/run.py
```

The first run may download the exact `renovate@44.5.0` npm package into the workstation npm cache. The runner creates a plain, temporary (non-Git) scan directory, writes one single-service Compose projection per service, invokes Renovate with `--platform=local --dry-run=lookup`, validates the one consumed debug record, normalizes it, verifies that projection bytes were unchanged, and deletes the scan directory.

For a non-interactive full run:

```bash
uv run --locked python prototypes/pinned-renovate-adapter/run.py --once
```

## Deliberate seam

- Input is a Renovate-independent scan request.
- Projection paths encode `<host>/<stack>/<service>` because Renovate's Docker Compose dependency records do not expose the Compose service name.
- Every projection contains exactly one service and a digest-pinned scan reference. That makes candidate digests visible for version updates and makes raw-record cardinality checkable.
- The adapter consumes only the `Renovate started` version record and the single `packageFiles with updates` record from JSON debug logs.
- A missing or malformed batch record fails the whole scan. A dependency warning produces one `lookup-failed` observation and makes only its stack incomplete.
- Exact-version tracks select an exact tag; floating tracks select a tag plus Renovate's top-level digest. Visible major alternatives are retained as facts but are not selected.
- The adapter emits observations only. It does not assign proposal readiness, aggregate a proposal, modify files, or call GitHub.

The fixture's stale Alpine digest is intentionally synthetic so the mutable `3.20` track always exercises a digest update. All candidate facts still come from the live registry lookup.
