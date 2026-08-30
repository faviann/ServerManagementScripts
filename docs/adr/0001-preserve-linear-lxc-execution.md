# Preserve linear LXC execution until dependencies are explicit

LXC lifecycle execution remains linear even after planning is separated from execution, and the first execution failure aborts remaining work for the targeted LXC set without attempting rollback. Independent per-LXC execution is deferred until stack and LXC dependencies—including required ordering, failure propagation, readiness, and reverse removal ordering—are represented explicitly and validated as an acyclic graph; Ansible's `free` strategy alone cannot provide those guarantees. A future dependency-aware scheduler may replace global fail-fast behavior with branch-scoped failure propagation, allowing genuinely independent branches to continue.

ADR-0009 amended this decision's command-level serialization rule. Mutating live operations remain linear, while audited read-only operations may run concurrently under shared machine-local locks.
