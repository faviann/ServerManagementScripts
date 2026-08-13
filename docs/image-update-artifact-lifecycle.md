# Image update planning artifacts

Image update planning has two durable, versioned artifact states:

- A **draft artifact** records normalized semantic evidence and one or more
  pending assessment requests. It cannot contain publishable GitHub actions.
- A **final artifact** records the same normalized evidence and the exact issue
  or comment Markdown that a later publisher may send to GitHub. It cannot
  contain assessment requests.

Both states bind the artifact to the `owner/name` repository identity, source
commit, and the relevant-input fingerprint for each selected repository input
snapshot. They record UTC creation and expiry timestamps; expiry is exactly 24
hours after creation. At the exact expiry timestamp the artifact is expired.

Writers validate against the kind's schema under
`schemas/image-update-artifacts/`, serialize deterministic JSON, and atomically
replace the destination only after all bytes have been written. Artifact fields
are deliberately narrow: credentials, recognizable secret material, raw
Renovate logs, and temporary scan paths are not artifact state.

## Resume and publish handshake

The writer returns the lowercase SHA-256 digest of the exact file bytes. Every
resume or publish consumer must receive that expected digest explicitly. It
also states whether it expects a draft or final lifecycle state. The consumer
hashes the bytes before parsing them and rejects any difference, then validates
the schema, lifecycle state, expiry, repository, source commit, and
relevant-input fingerprints.

The checksum is an **accidental-change handshake** between review and later
consumption. It catches edits, stale operator selections, and using the wrong
file. It is not a signature and is not a trust boundary against a hostile local
operator who can replace both the artifact and the expected checksum.
