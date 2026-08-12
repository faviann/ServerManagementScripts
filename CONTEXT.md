# Homelab Infrastructure Lifecycle

This context describes how the repository plans and applies changes to the managed homelab infrastructure.

## Language

**Image update track**:
An operator-selected version or release line used to discover eligible updates for one effective Compose image. An image-tracked stack can declare a shared default and service-specific exceptions, but every image-bearing service must have an intentional effective image update track.
_Avoid_: Channel, version inferred from the current tag

**Repository input snapshot**:
A read-only, schema-versioned record that authorizes the clone's current `HEAD` only when it equals GitHub's current default-branch commit, then fingerprints the checked-in policy, supported Compose inputs, and checked-in-policy-assisted runbook relevant to each operator-selected repo-managed stack. Unrelated worktree changes remain usable; changed relevant inputs make only their stack incomplete.
_Avoid_: Image update plan, deployed-state snapshot, validation result

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
