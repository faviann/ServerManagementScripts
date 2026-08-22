# Homelab Infrastructure Lifecycle

This context describes how the repository plans and applies changes to the managed homelab infrastructure.

## Language

**Image update track**:
An operator-selected version or release line used to discover eligible updates for one effective Compose image. An image-tracked stack can declare a shared default and service-specific exceptions, but every image-bearing service must have an intentional effective image update track.
_Avoid_: Channel, version inferred from the current tag

**Repository input snapshot**:
A read-only, schema-versioned record that authorizes the clone's current `HEAD` only when it equals GitHub's current default-branch commit, then fingerprints the checked-in policy, supported Compose inputs, and checked-in-policy-assisted runbook relevant to each operator-selected repo-managed stack. Unrelated worktree changes remain usable; changed relevant inputs make only their stack incomplete.
_Avoid_: Image update plan, deployed-state snapshot, validation result

**Repo-managed stack identity**:
The pair of inventory hostname and Compose project name that uniquely identifies one repo-managed stack across scans and image update proposals.
_Avoid_: Proposal title, image name

**Stack update policy**:
The operator-authored rules for detecting and preparing an image update proposal for one repo-managed stack. It identifies the upstream authority and update procedure for the stack, with optional per-image update-track and compatibility rules.
_Avoid_: Global image policy, deployment policy

**Stack tracking mode**:
The stack update policy choice between following independently managed image references and following an official upstream Compose baseline.
_Avoid_: Update ownership, portability owner

**Image-tracked stack**:
A repo-managed stack whose Compose definition is owned independently in this repository and whose update policy follows the container images referenced by that definition.
_Avoid_: Vendor-tracked stack, unmanaged stack

**Vendor-tracked stack**:
A repo-managed stack whose base Compose file deliberately follows a suitable official upstream Compose file, while homelab-specific behavior is isolated in an override layer. Its image update proposals compare the upstream baseline and identify any override adaptations required.
_Avoid_: Vendored stack, independently maintained stack

**Image update scan request**:
A Renovate-independent selection of validated stack and service identities, current image references, image update tracks, and candidate constraints. The Renovate adapter translates it into a scan projection and tool-specific configuration.
_Avoid_: Renovate configuration, stack update policy

**Image update scan projection**:
A disposable, stack-identity-preserving Compose representation generated from validated effective Compose models and stack update policies for a Renovate adapter run. It is lookup input only and never repository desired state.
_Avoid_: Generated stack, proposed Compose file, deployment manifest

**Renovate adapter**:
The pinned, read-only boundary that translates stack update policy into a local Renovate dry run and normalizes Renovate's results. Raw Renovate output and its version-specific shape do not cross this boundary.
_Avoid_: Renovate integration, registry client

**Image update candidate observation**:
A normalized account of one Compose service's current image and Renovate-selected candidate, including comparable digests, update classification, proposed exact reference, available release context, and lookup limitations. It supplies facts to proposal readiness evaluation but does not assign an outcome or change repository files.
_Avoid_: Renovate record, proposed patch, proposal readiness outcome

**Core candidate evidence**:
The minimum proof required before an image update candidate may produce or refresh a proposal: a valid effective policy and update track, comparable current and candidate digests, proof of a differing eligible candidate, and, for vendor-tracked stacks, an upstream Compose baseline resolved at a commit.
_Avoid_: Release enrichment, confidence context

**Targeted LXC set**:
The managed LXCs selected for a lifecycle run. Safety checks and the pre-action planning barrier apply to this set, not automatically to every LXC in inventory.
_Avoid_: Fleet, all LXCs

**Fleet preflight**:
The planning phase that validates cross-LXC invariants and shared infrastructure access, then provides a common observation of Proxmox state. It does not decide the lifecycle transition for an individual LXC.
_Avoid_: Per-LXC validation, lifecycle planning

**LXC identity reservation**:
An inventory claim on the VMID and hostname of a managed LXC, whether or not that LXC is targeted or currently exists. A targeted lifecycle run cannot use an identity reserved by another managed LXC.
_Avoid_: Runtime identity, active-container identity

**LXC lifecycle plan**:
A validated, non-executing description of the semantic transitions required to bring one targeted LXC from its observed state to its desired state, including destructive intent and reasons without exposing internal task names. It belongs only to the lifecycle run that produced it and is not reusable by a later run.
_Avoid_: Validation snapshot, action lists

**LXC lifecycle result**:
The semantic record of an LXC lifecycle plan and its observable execution outcome, with compact before-and-after observations. Every targeted LXC receives a result, including targets blocked from execution by another target's planning failure; results exclude internal task names, intermediate facts, and duplicated compiled contract data.
_Avoid_: Internal snapshot, action report

**LXC contract compilation**:
The single interpretation of layered inventory into the authoritative desired infrastructure state for one LXC. Infrastructure validation, provisioning, host configuration, and guest bootstrap consume the resulting compiled LXC contract rather than interpreting the inventory layers again.
_Avoid_: Flattening, spec merge

**Manual SSH recovery**:
An operator-initiated access restoration operation for an existing LXC. It remains usable when unrelated desired infrastructure state is invalid.
_Avoid_: Normal lifecycle configuration, full convergence

**Managed host configuration**:
The portion of an LXC's Proxmox-host configuration whose complete desired state is expressed by the compiled LXC contract. Manual changes within a managed category are not durable; configuration outside managed categories remains untouched.
_Avoid_: Additive configuration, minimum configuration

**Guest-command readiness**:
The proof that an LXC accepts a minimal command executed inside it through the Proxmox host. It is bounded by a deadline and is the only readiness managed host configuration establishes after a restart. It does not imply SSH access, boot completion, or application health; those remain owned by the modules that require them, and SSH access stays owned by the guest-configuration connection wait.
_Avoid_: Container running, boot complete, SSH ready

**LXC observation**:
A point-in-time representation of an LXC's current infrastructure and runtime state, used to compare reality with its compiled desired state.
_Avoid_: Validation snapshot, independently queried state

**Lifecycle planning barrier**:
The safety guarantee that no lifecycle actions begin until every targeted LXC has a valid LXC lifecycle plan. A planning failure for any target prevents actions for the entire targeted LXC set.
_Avoid_: Strict-validation mode, partial-skip execution

**Lifecycle policy**:
Persistent operator-authored rules that determine whether observed drift may produce destructive lifecycle transitions. A valid plan executes without a second per-run confirmation when its destructive transition is authorized by policy.
_Avoid_: Interactive approval, per-run confirmation

**Lifecycle intent**:
The exact set of infrastructure and configuration transitions permitted for a lifecycle run. If the requested outcome requires a transition outside that set, lifecycle planning fails rather than silently skipping it.
_Avoid_: Best-effort mode, lifecycle hint

**Configure-only lifecycle**:
A lifecycle run that reconciles configuration without creating or starting an LXC. Every targeted LXC must already be running or the run fails at the lifecycle planning barrier.
_Avoid_: Best-effort configuration, start-and-configure

**Provision-only lifecycle**:
A lifecycle run that reconciles LXC existence and managed host configuration without guest configuration. It starts an LXC when creating or rebuilding it but preserves the stopped state of an existing LXC.
_Avoid_: Ensure-running lifecycle, guest configuration

**Full lifecycle**:
A lifecycle run that reconciles both LXC infrastructure and guest configuration. It may start an existing stopped LXC because guest configuration requires the LXC to be running.
_Avoid_: Provision-only lifecycle, configure-only lifecycle

**Workstation setup marker**:
The record written by a completed workstation setup run, holding the identity of the inputs that run applied. It is the authority consulted before any work begins, so an input the marker does not record is an input no later run can detect a change to.
_Avoid_: Completion flag, sentinel file, done marker

**Workstation tool readiness**:
The proof that the workstation's required commands are present, runnable, and resolved from their managed locations. It says nothing about which declared configuration produced those commands.
_Avoid_: Environment healthy, validated environment, workstation configuration freshness

**Workstation configuration freshness**:
Whether the workstation's active Home Manager generation was built from the currently checked-out dotfiles source. It is evaluated against the local checkout only, never against the remote, and it is independent of workstation tool readiness — working tools prove nothing about it.
_Avoid_: Drift, environment health, in sync with origin
