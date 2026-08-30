from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTROLLER_LAYER = Path("playbooks/controller-prerequisites.yml")
PROXMOX_HOST_LAYER = Path("playbooks/proxmox-host-prerequisites.yml")
MIXED_LAYER = Path("playbooks/lifecycle-prerequisites.yml")
L3_CONSUMERS = {
    Path("site.yml"),
    Path("playbooks/add-ssh-keys-to-lxcs.yml"),
    Path("playbooks/configure-lxcs.yml"),
    Path("playbooks/lifecycle-lxcs.yml"),
    Path("playbooks/provision-lxcs.yml"),
    Path("playbooks/validate-infrastructure.yml"),
}
L1_ONLY_CONSUMERS = {
    Path("playbooks/lab-connectivity.yml"),
    Path("playbooks/proxmox_api_check.yml"),
    PROXMOX_HOST_LAYER,
    Path("playbooks/validate-credentials.yml"),
}
PlaybookGraph = dict[Path, list[dict[str, object]]]


def current_entrypoints() -> set[Path]:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--",
            "*.yml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        Path(name)
        for name in result.stdout.splitlines()
        if (REPO_ROOT / name).is_file()
        and (len(Path(name).parts) == 1 or Path(name).parent == Path("playbooks"))
    }


def documents(
    path: Path, graph: PlaybookGraph | None = None
) -> list[dict[str, object]]:
    if graph is not None:
        return graph[path]
    loaded = yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8")) or []
    assert isinstance(loaded, list), f"{path} must contain a playbook list"
    return loaded


def imports(path: Path, graph: PlaybookGraph | None = None) -> set[Path]:
    imported: set[Path] = set()
    for document in documents(path, graph):
        target = document.get("ansible.builtin.import_playbook")
        if target is not None:
            imported.add((path.parent / str(target)).resolve().relative_to(REPO_ROOT))
    return imported


def inherited_playbooks(
    path: Path, graph: PlaybookGraph | None = None
) -> set[Path]:
    inherited: set[Path] = set()
    pending = list(imports(path, graph))
    while pending:
        imported = pending.pop()
        if imported not in inherited:
            inherited.add(imported)
            pending.extend(imports(imported, graph))
    return inherited


def assert_expected_layer_relationships(graph: PlaybookGraph) -> None:
    for path in L3_CONSUMERS:
        assert PROXMOX_HOST_LAYER in inherited_playbooks(
            path, graph
        ), f"{path} requires effective L3"
    for path in L1_ONLY_CONSUMERS:
        inherited = inherited_playbooks(path, graph)
        assert CONTROLLER_LAYER in inherited, f"{path} requires effective L1"
        assert PROXMOX_HOST_LAYER not in inherited, f"{path} must remain L1-only"


def inherited_documents(
    path: Path, graph: PlaybookGraph | None = None
) -> list[dict[str, object]]:
    return [
        document
        for inherited in ({path} | inherited_playbooks(path, graph))
        for document in documents(inherited, graph)
    ]


def live_entrypoints(entrypoints: set[Path], graph: PlaybookGraph) -> set[Path]:
    return {
        path
        for path in entrypoints
        if path != CONTROLLER_LAYER
        and (
            PROXMOX_HOST_LAYER in ({path} | inherited_playbooks(path, graph))
            or any(
                document.get("hosts") not in (None, "localhost")
                for document in inherited_documents(path, graph)
            )
        )
    }


def test_every_derived_live_playbook_inherits_the_controller_layer() -> None:
    entrypoints = current_entrypoints()
    assert CONTROLLER_LAYER in entrypoints
    assert PROXMOX_HOST_LAYER in entrypoints
    assert MIXED_LAYER not in entrypoints
    graph = {path: documents(path) for path in entrypoints}

    live = live_entrypoints(entrypoints, graph)

    assert_expected_layer_relationships(graph)
    for path in live:
        assert CONTROLLER_LAYER in inherited_playbooks(path, graph), path

    assert Path("bootstrap.yml") not in live
    controller_source = (REPO_ROOT / CONTROLLER_LAYER).read_text(encoding="utf-8")
    assert "HOMELAB_IAC_LIFECYCLE_WRAPPER" in controller_source
    assert "HOMELAB_IAC_LIFECYCLE_WRAPPER" not in (
        REPO_ROOT / PROXMOX_HOST_LAYER
    ).read_text(encoding="utf-8")


def test_required_l3_consumer_cannot_substitute_l1() -> None:
    graph = {path: deepcopy(documents(path)) for path in current_entrypoints()}
    lifecycle = Path("playbooks/lifecycle-lxcs.yml")
    graph[lifecycle][0]["ansible.builtin.import_playbook"] = (
        "controller-prerequisites.yml"
    )

    with pytest.raises(AssertionError, match="requires effective L3"):
        assert_expected_layer_relationships(graph)


def test_l1_only_consumer_cannot_import_l3() -> None:
    graph = {path: deepcopy(documents(path)) for path in current_entrypoints()}
    connectivity = Path("playbooks/lab-connectivity.yml")
    graph[connectivity][0]["ansible.builtin.import_playbook"] = (
        "proxmox-host-prerequisites.yml"
    )

    with pytest.raises(AssertionError, match="must remain L1-only"):
        assert_expected_layer_relationships(graph)


def test_future_live_playbook_needs_l1_but_no_matrix_entry() -> None:
    graph = {path: deepcopy(documents(path)) for path in current_entrypoints()}
    future = Path("playbooks/future-live.yml")
    graph[future] = [
        {"ansible.builtin.import_playbook": "controller-prerequisites.yml"},
        {"hosts": "future_targets", "tasks": []},
    ]

    assert future not in L3_CONSUMERS | L1_ONLY_CONSUMERS
    live = live_entrypoints(set(graph), graph)
    assert future in live
    for path in live:
        assert CONTROLLER_LAYER in inherited_playbooks(path, graph), path


def test_current_entrypoints_exclude_nonignored_untracked_playbooks() -> None:
    relative = Path("playbooks/_untracked_entrypoint_test.yml")
    untracked = REPO_ROOT / relative
    assert not untracked.exists()
    try:
        untracked.write_text("---\n- hosts: future_targets\n", encoding="utf-8")
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", str(relative)],
            cwd=REPO_ROOT,
            check=False,
        )
        assert ignored.returncode == 1
        assert relative not in current_entrypoints()
    finally:
        untracked.unlink(missing_ok=True)
