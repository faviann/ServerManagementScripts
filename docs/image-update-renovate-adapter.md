# Image update Renovate adapter

`scripts/image_update_renovate_adapter.py` is the maintained read-only anti-corruption boundary around Renovate. It accepts one schema-version-1 JSON request file and writes only normalized candidate observations to stdout. Diagnostics go to stderr. It does not decide readiness, choose a vendor baseline, edit stacks, or interact with GitHub.

The request owns host, stack, and Compose-service identity; current effective image references and top-level digests; the permitted track; and vendor-provided candidate effective references and provenance when tracking mode is `vendor`. Each identity becomes exactly one digest-pinned projection at `projections/<host>/<stack>/<service>/compose.yaml` inside a disposable plain non-Git directory.

The versioned request, consumed raw-record, and normalized-output contracts are recorded under `schemas/image-update-renovate-adapter/`. Runtime validation also enforces cross-field identities and cardinalities that JSON Schema cannot express.

The adapter invokes exactly `renovate@44.5.0` once with the local platform and lookup dry-run. It strips inherited `RENOVATE_*` variables, supplies only its generated config file, and tells Renovate to ignore repository config discovery. It consumes only one exact-version, expected-level `Renovate started` JSON record and one `packageFiles with updates` JSON record. Process, version, request, raw-record, cardinality, identity, digest, projection-set, and projection-immutability failures invalidate the batch. A dependency lookup warning is instead a structured `lookup-failed` observation and makes only its owning stack incomplete. Raw logs, configuration, and projections remain temporary local diagnostics and are never returned. There is deliberately no fallback registry client: a failed or incompatible Renovate batch fails closed at this boundary.

Run it with:

```bash
uv run --locked python scripts/image_update_renovate_adapter.py <request.json>
```

Renovate upgrades require an explicit adapter-contract review and acceptance of the credential-free contract fixture at `tests/fixtures/image_update_renovate_adapter/contract-request.json`. Changing the package pin without reviewing the exact invocation, consumed JSON records, normalization, fail-closed cases, and real-boundary fixture is unsupported.
