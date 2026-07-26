# Resolving container-image discovery channels

## Question

Which registry, release, and upstream-Compose mechanisms can reliably turn a
mutable discovery channel such as `stable`, a major tag, or `latest` into an
exact human-readable version tag or an immutable digest? The answer must account
for multi-architecture images, authentication and rate limits, and registries
that cannot look up tags from a digest.

## Conclusion

The registry manifest digest is the only general, machine-verifiable identity
for the artifact selected by a discovery tag. OCI registries provide a direct
tag-to-digest operation, but the standard has no digest-to-tags operation. A
portable updater therefore needs this order:

1. Resolve the configured discovery tag with the registry manifest endpoint and
   record the returned top-level manifest digest and media type.
2. Generate plausible exact-version tag candidates from registry tags, a
   first-party release, or an upstream Compose file.
3. Resolve every candidate through the same registry mechanism, with the same
   content negotiation, and accept a human-readable tag only when its digest is
   exactly the discovery digest.
4. If no acceptable tag can be proved, propose the discovery result by digest.

GitHub releases and upstream Compose files are useful candidate authorities, but
neither proves which container artifact a tag names. The final equality check
must happen at the image registry.

An exact-looking tag such as `1.8.2` is readable, not inherently immutable. OCI
defines a tag as a human-readable pointer, and explicitly allows zero, one, or
many tags to point at a manifest; registries can also delete tags and manifests.
Only a digest pins content by protocol. Docker likewise documents digest pulls
as the way to pin an image to a specific version in time
([OCI Distribution definitions](https://github.com/opencontainers/distribution-spec/blob/main/spec.md#definitions),
[Docker digest pulls](https://docs.docker.com/reference/cli/docker/image/pull/#pull-an-image-by-digest-immutable-identifier)).

For this repository's proposed workflow, that supports two honest outputs:

- Prefer a verified exact-version tag for readable, reviewable routine updates
  when the stack policy accepts upstream tag mutability.
- Use `repository@sha256:...` when no matching exact tag exists or when the
  stack requires immutable desired state. Store the discovered version/channel
  separately in the proposal and stack metadata, because the Compose
  specification documents an image reference as either tag or digest, not a
  combined tag-and-digest form
  ([Compose `image`](https://github.com/compose-spec/compose-spec/blob/master/spec.md#image)).

## Registry mechanism

### Resolve a discovery tag

For `registry.example/repository:stable`, make a `HEAD` request to:

```text
/v2/<repository>/manifests/stable
```

OCI Distribution requires a successful manifest `HEAD` to return
`Docker-Content-Digest` and `Content-Length`. A `GET` of the same endpoint also
returns the manifest and requires the digest header. The client should send an
`Accept` header for the manifest/index types it understands and retain the
response `Content-Type`, because the endpoint performs content negotiation
([pulling and checking manifests](https://github.com/opencontainers/distribution-spec/blob/main/spec.md#pulling-manifests)).

Practical requirements for the updater:

- Prefer `HEAD` for routine checks; fall back to `GET` when an older registry
  omits the digest header or when the body is needed for platform inspection.
- When using `GET`, verify the returned bytes against
  `Docker-Content-Digest`, as the specification requires clients that use the
  header to do.
- Cache `(registry, repository, reference, Accept set) -> digest, media type,
  ETag/last-modified if supplied, checked-at`.
- Treat `401`, `403`, `404`, `405`, and `429` as distinct outcomes rather than
  as "no update": credentials needed, forbidden, absent reference, unsupported
  method (retry with `GET`), and throttled.

### Reverse lookup is an enumeration problem

OCI Distribution standardizes `GET /v2/<name>/tags/list`, including pagination
with `n`, `last`, and preferably the returned `Link: ...; rel="next"` header.
It does **not** standardize a query that accepts a digest and returns its tags
([listing tags](https://github.com/opencontainers/distribution-spec/blob/main/spec.md#listing-tags)).
The portable reverse lookup is therefore:

1. List all tags, following every `Link` page.
2. Filter names locally using the stack's allowed version grammar. Exclude
   obvious channels (`latest`, `stable`, `edge`), prereleases, platform suffixes,
   and disallowed majors unless policy says otherwise.
3. Resolve the remaining tags with manifest `HEAD` requests.
4. Keep tags whose negotiated top-level digest equals the discovery digest.
5. Rank only the equal tags, preferring the most specific version form
   (`1.8.2` over `1.8` over `1`) and the stack's configured prefix convention.

This can be expensive for repositories with thousands of tags. First-party
release or Compose metadata should narrow the candidate set before full
enumeration, and resolved tag/digest pairs should be cached. Full enumeration
is a fallback, not the first request on every run.

Provider APIs can optimize this process, but must be optional adapters:

- GitHub's Packages REST representation for a container package version uses a
  digest-like `name` and returns the tags attached to it in
  `metadata.container.tags`. Listing package versions and finding the discovery
  digest therefore gives GHCR a practical digest-to-tags lookup. Access to
  package metadata requires `read:packages`
  ([GitHub Packages versions](https://docs.github.com/en/rest/packages/packages#list-package-versions-for-an-organization)).
- GitLab's project Container Registry API lists repository tags and its
  per-tag-details endpoint returns each tag's `digest`. That reduces the adapter
  to one documented metadata call per candidate tag, but it is still
  tag-to-digest enumeration rather than digest-to-tags
  ([GitLab Container Registry API](https://docs.gitlab.com/api/container_registry/#retrieve-details-of-a-registry-repository-tag)).
- Do not depend on a Docker Hub digest-to-tags API. Docker's release notes say
  its Advanced Image Management endpoint
  `/namespaces/{namespace}/repositories/{repository}/images/{digest}/tags` was
  retired in December 2023
  ([Docker Hub release notes](https://docs.docker.com/docker-hub/release-notes/#2023-12-11)).
  Docker Hub's current API is useful for tag metadata and pagination, but its
  API rate limit is separate from both pull limits and anti-abuse limits
  ([Docker Hub API limits](https://docs.docker.com/reference/api/hub/latest/#tag/rate-limiting)).

Even when a provider API supplies the association, verify the selected tag once
through the registry manifest endpoint. That keeps the proof path uniform and
catches stale provider metadata or a media-type/digest mismatch.

### OCI annotations are hints, not proof

OCI defines optional `org.opencontainers.image.version`,
`org.opencontainers.image.revision`, and `org.opencontainers.image.source`
annotations. The version *may* match a source tag and *may* be SemVer-compatible;
neither is required. An updater may use these values to generate release/tag
candidates, but must still verify a candidate at the registry
([OCI predefined annotations](https://github.com/opencontainers/image-spec/blob/main/annotations.md#pre-defined-annotation-keys)).

For a single-platform image, these annotations commonly live in the config blob,
which requires fetching the manifest and then its referenced config blob. For an
image index they may instead be on the index or individual descriptors. Missing
or conflicting values should lower confidence rather than fail detection.

## Multi-architecture images

An OCI image index is a top-level manifest containing descriptors for one or
more platform manifests. Each descriptor has its own digest and may identify an
OS, architecture, and variant
([OCI image index](https://github.com/opencontainers/image-spec/blob/main/image-index.md)).
Consequently one multi-architecture tag can expose:

- one **index digest**, representing the whole published platform set; and
- several **platform-manifest digests**, representing `linux/amd64`,
  `linux/arm64`, and so on.

The updater must compare like with like:

- Default comparison: discovery index digest to candidate index digest. Equality
  proves both tags select the same complete multi-platform publication.
- A discovery tag resolving to an index and a candidate resolving to a
  single-platform manifest are not equal, even if that manifest is one member
  of the index.
- Platform-specific comparison is allowed only when the stack explicitly fixes
  `services.<name>.platform`. Fetch the index, select the matching descriptor by
  OS/architecture/variant, and label the result as platform-specific. Do not
  present it as equality of the whole image.

Use the same ordered `Accept` set for every tag resolution. Otherwise content
negotiation can return an index for one request and a platform manifest or
different representation for another, making digest comparison meaningless.
The OCI Distribution spec requires clients to advertise supported types and
check the response `Content-Type`
([manifest content negotiation](https://github.com/opencontainers/distribution-spec/blob/main/spec.md#pulling-manifests)).
A mature registry client should also accept the Docker schema-2 manifest and
manifest-list media types needed for older, OCI-compatible registries.

The safest immutable Compose pin for a portable multi-architecture stack is the
**top-level index digest**. Docker's own pull-limit accounting also distinguishes
a multi-architecture pull as one pull per architecture, which is another reason
for detection to stop at manifest metadata rather than downloading layers
([Docker Hub pull definition](https://docs.docker.com/docker-hub/usage/pulls/#pull-definition)).

## Releases as candidate authorities

For projects that designate GitHub Releases as their release authority:

1. Query `GET /repos/<owner>/<repo>/releases/latest`, or list releases when the
   stack tracks a major line.
2. Read `tag_name`, prerelease/draft state, release body, and URLs.
3. Transform `tag_name` to image-tag candidates using an explicit per-stack
   mapping (for example `v1.8.2 -> 1.8.2`), never an assumed global rule.
4. Resolve the candidates at the image registry and require digest equality with
   the discovery channel.

GitHub defines "latest release" as the most recent non-draft, non-prerelease
release by `created_at`, and maintainers can control whether a release is marked
latest. This is not necessarily the numerically greatest SemVer
([GitHub latest release endpoint](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)).
The endpoint also omits ordinary Git tags that have no associated release
([GitHub list releases](https://docs.github.com/en/rest/releases/releases#list-releases)).
Therefore:

- use Releases only when the vendor treats them as authoritative;
- implement major-channel selection by parsing all eligible releases, not by
  blindly taking `/latest`;
- treat a release tag as evidence for a candidate and release notes, not as a
  container identity;
- report a release with no matching registry artifact as a metadata mismatch
  requiring assistance, not as an image update.

OCI `version`, `revision`, and `source` annotations can strengthen the
release-to-image relationship, but digest equality remains the proof.

## Upstream Compose as a candidate authority

For a vendor-tracked stack, the most reliable upstream-Compose mechanism is:

1. Select the upstream release according to the vendor's documented channel.
2. Resolve its Git tag/ref to a commit SHA.
3. Fetch the Compose path at that exact SHA, not from the default branch or a
   floating `latest` URL. GitHub's contents endpoint accepts a branch, tag, or
   commit in `ref` and can return raw file content
   ([GitHub repository contents](https://docs.github.com/en/rest/repos/contents#get-repository-content)).
4. Record the release/tag, commit SHA, source URL, path, and content hash in the
   proposal.
5. Parse every upstream `image:` reference. Exact tags become candidates;
   floating tags are resolved through the registry; digest references are
   already immutable but should still be checked for existence.
6. Compare the new upstream base with the locally retained base, apply the local
   override, and validate the merged model with `docker compose config`.

Docker documents `compose.yaml` as the base and `compose.override.yaml` as the
local customization layer; it also documents that later files merge or override
earlier ones and that relative paths are based on the first file
([Compose file merge](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)).
This supports preserving the vendor file closely while keeping homelab
customizations separate.

An upstream Compose file can be authoritative for the **stack shape**—services,
dependencies, settings, and the vendor's chosen image references—but it still
does not prove that a Git release and an image tag contain the same build.
Registry resolution supplies that proof.

Fallback to manually assisted review when the upstream file:

- exists only on a mutable branch or documentation page;
- is generated by an installer and has no stable source path;
- depends on release-specific `.env` values or downloaded fragments that cannot
  be reproduced;
- uses build contexts rather than published images;
- changes service topology, volumes, database majors, or required secrets; or
- cannot be merged with the local override into a valid Compose model.

## Authentication and request budgets

OCI/Docker registries commonly begin with `401 Unauthorized` and a
`WWW-Authenticate: Bearer` challenge containing a token realm, service, and
repository scope. The client obtains a token and retries with
`Authorization: Bearer ...`
([Distribution token authentication](https://distribution.github.io/distribution/spec/auth/token/)).
The updater should use a registry client that implements this challenge flow,
request read-only/pull scope, cache short-lived tokens in memory, and never put
credentials in `stack.yaml`, issues, logs, or URLs.

Registry-specific facts that affect a homelab-wide scan:

- GHCR permits anonymous access to public container images. Private images need
  appropriate package access; outside Actions, GitHub documents a classic PAT
  with at least `read:packages`
  ([GHCR authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-to-the-container-registry)).
- Docker Hub currently allows 100 pulls per six hours for unauthenticated
  clients and 200 for authenticated Personal accounts. Its documentation says
  a manifest `HEAD` can inspect remaining limits without counting as a pull,
  whereas `GET` emulates a pull. A separate abuse limit can still return `429`
  ([Docker Hub pull limits](https://docs.docker.com/docker-hub/usage/pulls/#view-pull-rate-and-limit)).
- Docker Hub API calls have a separate per-minute budget exposed by
  `X-RateLimit-*`; on `429`, honor `Retry-After`
  ([Docker Hub API limits](https://docs.docker.com/reference/api/hub/latest/#tag/rate-limiting)).
- Public GitHub REST calls are limited to 60 requests/hour unauthenticated;
  authenticated user requests normally get 5,000/hour. GitHub can also apply
  secondary limits and requires clients to honor `Retry-After` and
  `x-ratelimit-reset`
  ([GitHub REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)).
- Authenticated conditional GitHub requests that return `304 Not Modified` do
  not count against the primary limit. Cache ETags for releases and upstream
  files and send `If-None-Match`
  ([GitHub REST best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#use-conditional-requests-if-appropriate)).

The updater should serialize or modestly bound requests per registry, cache
tag pages and tag-to-digest results, use exponential backoff only where the
provider permits it, and return an "incomplete scan" state when throttled. It
must never interpret throttling as "up to date."

## Decision table

| Evidence available | Reliable output | Confidence and action |
|---|---|---|
| Discovery tag and matching exact-version tag have equal top-level digests | Exact version tag; optionally choose digest when policy requires immutability | High; routine proposal |
| Discovery tag resolves, no acceptable matching version tag | Top-level digest | High artifact identity, low release context; routine only for policies that allow digest-only updates |
| GitHub release maps to a registry tag whose digest equals discovery | Exact version plus release notes | High; routine proposal subject to stack risk |
| Upstream Compose at an exact commit supplies references that registry resolution verifies | Vendor base diff plus verified image identities | High; validate merged Compose and surface topology/data changes |
| Version/revision annotations exist but no matching tag is resolvable | Digest plus annotations as hints | Medium; manually assisted unless policy explicitly permits |
| Candidate tag matches only one platform descriptor inside a discovery index | Platform digest only for an explicitly platform-pinned stack | Medium/high for that platform; never claim whole-index equality |
| Registry denies tag listing but discovery `HEAD` works | Digest only | High identity; reverse lookup unavailable |
| Release/Compose claims a version but registry digest differs | No automatic version mapping | Conflict; manually assisted |
| Registry or GitHub is rate-limited/unreachable | No conclusion | Incomplete scan; retry later |

## Recommended contract for the future updater

Each image policy should declare enough intent that discovery does not infer
vendor semantics:

```yaml
updates:
  mode: image-tracked # or vendor-tracked
  images:
    app:
      repository: ghcr.io/example/app
      discovery:
        tag: stable
      version:
        source: github-release # registry-tags, upstream-compose, or manual
        repository: example/app
        tag_transform: strip-v
        allow_prerelease: false
      allowed_major: 3
      platform: null
      digest_only_without_release_notes: true
```

Vendor-tracked stacks additionally need an upstream repository, Compose path,
release selector, and last accepted upstream commit SHA.

The proposal record should include the discovery tag, old and new negotiated
digest/media type, chosen readable version if proved, all matching tags,
platform scope, release/upstream-Compose evidence, request failures, and a
confidence classification. A major-version candidate should be reported
separately and must not mutate `allowed_major`.

The process should be deterministic and fail closed:

- registry equality decides image identity;
- stack metadata decides which evidence and risk level are acceptable;
- releases and Compose decide context and candidate names;
- missing reverse lookup degrades to a digest proposal rather than guessing;
- low-confidence stateless updates may be proposed if policy allows them;
- database, foundational, topology-changing, or contradictory updates remain
  manually assisted.
