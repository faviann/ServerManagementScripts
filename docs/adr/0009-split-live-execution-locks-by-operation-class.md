# Split live-execution locks by operation class

Live operations take a machine-local lock according to their effect: mutating operations take an exclusive lock, while audited read-only operations take a shared lock. In particular, `./run.sh --check` is read-only and uses the shared class; ordinary full, provision-only, and configure-only lifecycle runs remain exclusive.

This permits concurrent observations without allowing a read to overlap a mutation. A caller that cannot acquire its lock must fail immediately with status 75 and report the holding process and worktree. The lock deliberately does not coordinate different control nodes, has no waiting mode, and does not encode its class in the wrapper marker.

This decision amends ADR-0001's linear-execution model: linear execution continues to govern mutation, but it no longer serializes audited read-only live work with other readers.
