# Existing tooling for policy-driven stack update proposals

## Question

How much of the desired policy-driven, stack-scoped detection and GitHub
proposal workflow can existing tools such as Renovate provide, and what
irreducible behavior—if any—would justify custom skill logic?

The target workflow is intentionally narrower than deployment automation:

- each stack declares an update policy;
- a run scans all stacks, one host, or one stack;
- discovery may follow `stable`, a major line, `latest`, an upstream Compose
  file, or official upgrade documentation;
- desired state prefers an exact version and otherwise uses a channel plus
  digest;
- major upgrades are surfaced but never silently adopted;
- the output is one durable GitHub proposal per affected stack; and
- no proposal changes or deploys the stack.

## Conclusion

**Use Renovate as the default update engine and keep custom skill logic as an
orchestrator/advisor, not as a second container registry client.** Renovate
already provides Compose discovery, Docker tag and digest lookups, version
filtering, major/minor separation, release-note enrichment, path-sensitive
rules, grouping, scheduling, and reviewable diffs. Reimplementing those
capabilities would conflict with the repository's preference for less code.

There is one consequential product mismatch: Renovate's proposal is a branch
and pull request. Its issue-based Dependency Dashboard is a single
repository-wide index, not one proposal issue per stack. The lowest-code
workflow is therefore **one Renovate pull request per stack**, with major or
manual updates held behind Dependency Dashboard approval. If one GitHub issue
per stack remains a hard requirement, custom logic is justified for the issue
lifecycle and narrative only; it should not duplicate Renovate's registry and
version resolver without first proving that Renovate cannot supply the
candidate.

Custom behavior remains justified for:

1. translating `stack.yaml` policy into Renovate-compatible rules;
2. deciding whether an update requires official upgrade-document review;
3. comparing or synchronizing a vendor-owned Compose baseline and validating
   local overrides;
4. rendering and refreshing a stack-scoped GitHub issue if issues remain the
   required proposal artifact; and
5. optionally resolving a floating discovery tag to an equivalent readable
   version tag by digest, because Renovate natively preserves/pins the selected
   tag rather than documenting a channel-to-equivalent-version promotion.

That fifth item should be an adapter used only where the readable-version
preference matters. `stable@sha256:...` is already immutable and Renovate can
maintain it; accepting that form eliminates substantial custom registry logic.

## Capability fit

| Required behavior | Renovate | Dependabot | Diun / Watchtower | Custom behavior still needed |
| --- | --- | --- | --- | --- |
| Find images in Compose | Native Compose manager | Native `docker-compose` ecosystem | Runtime/file discovery, depending on tool | No |
| Update exact image tags | Yes | Yes, primarily SemVer | Detects runtime/tag changes; does not edit Git | No |
| Pin and update image digests | Yes | Not a suitable digest-tracking engine for this workflow | Detects digest changes | No if Renovate is used |
| Follow current major and separately surface next major | Native filtering and separate major PRs | SemVer update-type filters | Not a Git proposal model | Policy translation only |
| One coordinated proposal per stack | Groups matching stack paths, but normally separates major from non-major proposals | Groups updates into PRs, with more limited configuration | No Git diff | Yes for the strict single-artifact contract |
| Release notes/changelog evidence | Often, when upstream metadata connects image and source | Often, under similar OCI/source-tag conditions | Notification-oriented | Manual fallback and upgrade-doc interpretation |
| Vendor Compose tracking | Complement with `vendir`; not a native Compose import/merge | No comparable first-party path found | No | Baseline/override validation and policy |
| One GitHub issue per affected stack | No; one aggregate Dependency Dashboard issue | No; creates PRs | Notifications/webhooks, not proposal issues | Yes |
| Read arbitrary `stack.yaml` operational policy | No native policy contract | No | No | Yes, or generate conventional tool config |

## What Renovate already provides

### Compose and registry update mechanics

Renovate's Compose manager scans files matching Compose naming conventions and
extracts Docker image dependencies. Its official documentation says it matches
`compose*.yml`/`docker-compose*.yml` forms and uses the Docker datasource
([Compose manager](https://docs.renovatebot.com/modules/manager/docker-compose/)).
The current extractor source confirms that it parses Compose services and
extracts non-built image references
([source](https://github.com/renovatebot/renovate/blob/9d3b990782f350ecdc094a53d033ad35c2b60713/lib/modules/manager/docker-compose/extract.ts#L31-L80)).
Its replacement template preserves both a tag and a digest as
`image:tag@sha256:...`
([source test](https://github.com/renovatebot/renovate/blob/9d3b990782f350ecdc094a53d033ad35c2b60713/lib/modules/manager/docker-compose/extract.spec.ts#L42-L56)).

For version-like Docker tags, Renovate queries the registry and selects updates.
It can disable major updates, preserve tag precision, and pin images by digest
([Docker support](https://docs.renovatebot.com/docker/),
[pinDigests](https://docs.renovatebot.com/configuration-options/#pindigests)).
This covers:

- exact version to newer exact version;
- a major tag or exact version constrained by `allowedVersions`;
- initial conversion from a tag to `tag@digest`; and
- later changes to the digest behind the same floating tag.

Renovate's Docker datasource treats tag and digest updates as distinct concepts;
its default digest branch topic includes the current tag
([datasource source](https://github.com/renovatebot/renovate/blob/9d3b990782f350ecdc094a53d033ad35c2b60713/lib/modules/datasource/docker/index.ts#L60-L82)).
That is a good fit for maintaining `stable@sha256:...` or
`latest@sha256:...`, but it is not evidence of the desired reverse lookup:
resolve `stable`, enumerate other tags with the same equivalent manifest
digest, choose an immutable version tag, and replace `stable` with that tag.
That promotion remains a narrow custom adapter if it is required.

Docker tags are inconsistent. Renovate explicitly supports per-package
versioning overrides because Docker and GitHub tag schemes cannot always be
inferred correctly
([versioning guidance](https://docs.renovatebot.com/modules/versioning/)).
That maps naturally to per-stack or per-image policy, although the policy must
be expressed as Renovate `packageRules` or generated into them.

### Stack grouping and major-version safety

`packageRules.matchFileNames` can target dependencies by path, and every update
with the same `groupName` is placed in one branch/PR
([configuration](https://docs.renovatebot.com/configuration-options/#packagerulesmatchfilenames),
[groupName](https://docs.renovatebot.com/configuration-options/#groupname)).
A rule per `stacks/<host>/<stack>/**` can therefore coordinate an app, database,
cache, and sidecars in a stack-scoped PR.

Renovate separates major and minor updates by default. When both are available,
it creates distinct proposals, preserving the in-major update while also
surfacing the next major
([separateMajorMinor](https://docs.renovatebot.com/configuration-options/#separatemajorminor)).
Major updates—or particular packages such as databases—can require a checkbox
approval in the Dependency Dashboard before Renovate creates their PR
([approval workflow](https://docs.renovatebot.com/key-concepts/dashboard/#require-approval-for-major-updates)).
These controls implement the mechanical part of "surface, never silently
adopt." They do not implement the destination's strict **one artifact per
stack** rule when an in-major update and next-major update coexist: Renovate
deliberately creates separate major and minor PRs. Setting
`separateMajorMinor: false` selects only the latest release instead of retaining
both choices, so a single issue that presents the routine update and separately
highlights the major migration remains custom presentation logic.

Release-note enrichment is available when the image exposes an OCI source URL
and the source repository has corresponding releases/tags. Renovate documents
both OCI-based source discovery and configurable changelog links
([Docker datasource](https://docs.renovatebot.com/modules/datasource/docker/),
[fetchChangeLogs](https://docs.renovatebot.com/configuration-options/#fetchchangelogs)).
It cannot decide that Authentik's prose upgrade guide must be read, interpret
the guide, or explain the homelab-specific consequences. That is advisory
skill work.

### The issue-versus-PR mismatch

Renovate's documented workflow culminates in applying updates to branches and
creating pull requests
([workflow](https://docs.renovatebot.com/key-concepts/how-renovate-works/)).
The Dependency Dashboard creates one issue containing the repository's pending,
ignored, and approval-gated updates
([dashboard](https://docs.renovatebot.com/key-concepts/dashboard/)).
Configuration can categorize sections of that issue, but the documented model
is still one dashboard issue, not one issue per dependency group.

This produces a clear choice:

- **Prefer less custom code:** adopt one Renovate PR per stack as the durable
  proposal. PRs already contain the exact diff and can remain unmerged until
  reviewed.
- **Preserve the current destination exactly:** write skill logic that creates
  or refreshes one issue per stack. The skill must then obtain candidate data
  and build a proposed diff. Renovate has no documented stable "proposal JSON"
  API, so treating its logs or internal objects as an integration contract
  would be fragile. A prototype would be necessary before selecting Renovate
  as a headless backend for issue generation.

## Vendor-tracked Compose files

Renovate updates dependencies *inside* a Compose file; it does not natively
import an upstream Compose file and reconcile it with a local copy.

Carvel `vendir` is a directly relevant complement. It declaratively syncs
selected paths from Git repositories and records resolved commits in a lock
file
([vendir overview](https://carvel.dev/vendir/),
[sync and locks](https://carvel.dev/vendir/docs/develop/sync/)).
It supports including only selected source paths, so an upstream `compose.yaml`
can be isolated from unrelated repository content
([vendir specification](https://carvel.dev/vendir/docs/latest/vendir-spec/)).
Renovate has a native vendir manager that updates explicit Git refs and GitHub
release tags and maintains `vendir.lock.yml`
([Renovate vendir manager](https://docs.renovatebot.com/modules/manager/vendir/)).

This combination could remove custom download/version-selection code for
vendor-tracked stacks. It does **not** remove the need to:

- determine whether upstream Compose is suitable;
- preserve the local override;
- show the meaningful upstream baseline diff;
- validate `docker compose config` against the override; and
- read migration documentation.

It also adds a new repository tool and lockfile contract. A small prototype on
one vendor-tracked stack should decide whether that is less complexity than a
simple skill-managed upstream fetch.

## First-party alternatives

### Dependabot

GitHub now lists Docker Compose as a supported, GitHub-maintained ecosystem.
Dependabot creates update pull requests, parses Docker tags as SemVer, and can
enrich Docker updates with release notes/changelogs when OCI source metadata
and source tags align
([supported ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories)).
It can group matching dependencies and filter groups by major, minor, and patch
update type
([options reference](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference#groups)).

Dependabot is attractive because it is hosted directly by GitHub, but it offers
no advantage for this policy shape: its output is still PRs, its versioning and
per-package policy surface is narrower, it has no equivalent to Renovate's
custom managers/vendir integration, and it does not supply per-stack proposal
issues. Renovate is the better fit.

### Diun and Watchtower

Diun checks tracked image tags/digests on a schedule and sends notifications. It
can filter repository tags and discover images from Docker or files
([Diun](https://crazymax.dev/diun/)). Its output is notifications/webhooks, not
repository diffs or coordinated Git proposals.

Watchtower discovers running containers through a Docker daemon. Monitor-only
mode can notify without restarting containers, although its documentation notes
that it may still pull images to determine changes
([Watchtower monitor-only](https://containrrr.dev/watchtower/arguments/#without-updating-containers)).
Labels and scopes can select containers
([container selection](https://containrrr.dev/watchtower/container-selection/)).
That runtime-centric model bypasses declared Git desired state and cannot
produce the required Compose proposal.

Neither tool should be added for this workflow. They overlap only the detection
step and would leave all policy, diff, and issue work custom.

## Recommended boundary for the future skill

The implementation specification should make the skill an orchestration layer:

1. validate and select stack policies from `stack.yaml`;
2. map ordinary image policies to Renovate configuration;
3. invoke or rely on Renovate for registry lookup, version classification,
   digest maintenance, grouping, and the actual Compose diff;
4. invoke a vendor-source adapter—potentially vendir—for vendor-tracked stacks;
5. gather/interpret official upgrade documentation only for policies that
   require manual assistance; and
6. publish the chosen artifact.

Before specifying issue automation, resolve one product decision: **is a
stack-scoped Renovate PR an acceptable proposal, or is a separate proposal
issue mandatory?** Choosing PRs deletes the largest custom subsystem. If issues
are mandatory, prototype the candidate-data boundary before committing to a
full custom registry resolver.

The later prototype should also test three concrete cases:

- exact version within a major line plus a separately visible next major;
- a floating `stable@digest` update, including multi-architecture manifest
  behavior; and
- one vendor-tracked stack with an upstream Compose file and local override.
