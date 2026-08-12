import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from stack_update_policy import (  # noqa: E402
    GitHubReadError,
    GitHubRepositoryState,
    build_repository_snapshot,
)


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def create_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Snapshot Test")
    git(repository, "config", "user.email", "snapshot@example.invalid")
    git(repository, "remote", "add", "origin", "https://github.com/example/homelab.git")
    stack = repository / "stacks/media/example"
    stack.mkdir(parents=True)
    (stack / "stack.yaml").write_text(
        "updates:\n  mode: images\n  track: stable\n", encoding="utf-8"
    )
    (stack / "compose.yaml").write_text(
        "services:\n  app:\n    image: example/app:1\n", encoding="utf-8"
    )
    git(repository, "add", ".")
    git(repository, "commit", "-m", "initial")
    return repository, git(repository, "rev-parse", "HEAD")


def create_compose_gitlink(repository: Path) -> tuple[Path, str, str, str]:
    compose_path = "stacks/media/example/compose.yaml"
    compose = repository / compose_path
    compose.unlink()
    compose.mkdir()
    git(compose, "init", "-b", "main")
    git(compose, "config", "user.name", "Snapshot Test")
    git(compose, "config", "user.email", "snapshot@example.invalid")
    (compose / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    git(compose, "add", "compose.yaml")
    git(compose, "commit", "-m", "first compose input")
    first_commit = git(compose, "rev-parse", "HEAD")
    (compose / "compose.yaml").write_text(
        "services:\n  changed: {}\n", encoding="utf-8"
    )
    git(compose, "add", "compose.yaml")
    git(compose, "commit", "-m", "second compose input")
    second_commit = git(compose, "rev-parse", "HEAD")
    git(compose, "checkout", "--detach", first_commit)
    git(repository, "add", compose_path)
    git(repository, "commit", "-m", "use gitlink compose input")
    return compose, compose_path, first_commit, second_commit


def test_clean_default_branch_checkout_has_publishable_stack_snapshot(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    assert result.snapshot.repository == "example/homelab"
    assert result.snapshot.default_branch == "main"
    assert result.snapshot.commit == commit
    assert len(result.snapshot.stacks) == 1
    stack = result.snapshot.stacks[0]
    assert stack.identity == "stacks/media/example"
    assert stack.complete is True
    assert stack.changed_inputs == ()
    assert stack.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/stack.yaml",
    )
    assert len(stack.fingerprint) == 64
    assert result.as_dict() == {
        "errors": [],
        "schema_version": 1,
        "snapshot": {
            "commit": commit,
            "default_branch": "main",
            "repository": "example/homelab",
            "stacks": [
                {
                    "changed_inputs": [],
                    "complete": True,
                    "fingerprint": stack.fingerprint,
                    "identity": "stacks/media/example",
                    "relevant_inputs": [
                        "stacks/media/example/compose.yaml",
                        "stacks/media/example/stack.yaml",
                    ],
                }
            ],
        },
        "valid": True,
    }


def test_assisted_runbook_alias_of_compose_is_snapshotted_once(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    (stack / "stack.yaml").write_text(
        """\
updates:
  mode: images
  track: stable
  procedure:
    mode: assisted
    runbook: compose.yaml
""",
        encoding="utf-8",
    )
    git(repository, "add", "stacks/media/example/stack.yaml")
    git(repository, "commit", "-m", "add aliased assisted runbook")
    commit = git(repository, "rev-parse", "HEAD")
    reader = lambda owner, name: GitHubRepositoryState("main", commit)

    clean = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )
    (stack / "compose.yaml").write_text("locally changed\n", encoding="utf-8")
    dirty = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )

    assert clean.snapshot is not None
    assert dirty.snapshot is not None
    clean_stack = clean.snapshot.stacks[0]
    dirty_stack = dirty.snapshot.stacks[0]
    assert clean_stack.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/stack.yaml",
    )
    assert clean_stack.changed_inputs == ()
    assert dirty_stack.relevant_inputs == clean_stack.relevant_inputs
    assert dirty_stack.changed_inputs == ("stacks/media/example/compose.yaml",)
    assert clean_stack.fingerprint == dirty_stack.fingerprint
    assert clean_stack.fingerprint == (
        "4fe45a5f169794327bfdd37680f800f2ab5978ab790318d711c0eefc160af581"
    )


@pytest.mark.parametrize(
    "selected_stacks",
    [["stacks/media/does-not-exist"], [None]],
)
def test_checkout_not_at_remote_default_branch_commit_fails_before_stack_inspection(
    tmp_path: Path, selected_stacks: list[object]
) -> None:
    repository, commit = create_repository(tmp_path)
    git(repository, "checkout", "-b", "feature")
    (repository / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(repository, "add", "feature.txt")
    git(repository, "commit", "-m", "feature")

    result = build_repository_snapshot(
        repository,
        selected_stacks,  # type: ignore[arg-type]
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is None
    assert [(error.code, error.path) for error in result.errors] == [
        ("unauthorized-head", "repository.head")
    ]


def test_unrelated_dirtiness_does_not_change_publishable_stack_snapshot(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)
    reader = lambda owner, name: GitHubRepositoryState("main", commit)
    clean = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )
    (repository / "notes.txt").write_text("local work\n", encoding="utf-8")

    dirty = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )

    assert dirty.errors == ()
    assert dirty.snapshot is not None
    assert clean.snapshot is not None
    assert dirty.snapshot.stacks == clean.snapshot.stacks


def test_changed_relevant_input_makes_only_its_stack_incomplete(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)
    second = repository / "stacks/media/second"
    second.mkdir()
    (second / "stack.yaml").write_text(
        "updates:\n  mode: images\n  track: stable\n", encoding="utf-8"
    )
    (second / "compose.yml").write_text(
        "services:\n  app:\n    image: example/second:1\n", encoding="utf-8"
    )
    git(repository, "add", ".")
    git(repository, "commit", "-m", "second stack")
    commit = git(repository, "rev-parse", "HEAD")
    first_compose = repository / "stacks/media/example/compose.yaml"
    first_compose.write_text("locally changed\n", encoding="utf-8")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/second", "stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    assert [stack.identity for stack in result.snapshot.stacks] == [
        "stacks/media/example",
        "stacks/media/second",
    ]
    first, second_snapshot = result.snapshot.stacks
    assert first.complete is False
    assert first.changed_inputs == ("stacks/media/example/compose.yaml",)
    assert len(first.fingerprint) == 64
    assert second_snapshot.complete is True
    assert second_snapshot.changed_inputs == ()


def test_mode_only_worktree_change_makes_stack_incomplete(tmp_path: Path) -> None:
    repository, commit = create_repository(tmp_path)
    compose_path = "stacks/media/example/compose.yaml"
    compose = repository / compose_path
    compose.chmod(compose.stat().st_mode | 0o111)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    stack = result.snapshot.stacks[0]
    assert stack.complete is False
    assert stack.changed_inputs == (compose_path,)


def test_committed_mode_only_change_changes_stack_fingerprint(tmp_path: Path) -> None:
    repository, commit = create_repository(tmp_path)
    compose = repository / "stacks/media/example/compose.yaml"
    original = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )
    compose.chmod(compose.stat().st_mode | 0o111)
    git(repository, "add", "stacks/media/example/compose.yaml")
    git(repository, "commit", "-m", "make compose executable")
    mode_commit = git(repository, "rev-parse", "HEAD")

    changed = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", mode_commit),
    )

    assert original.snapshot is not None
    assert changed.snapshot is not None
    assert (
        original.snapshot.stacks[0].fingerprint
        != changed.snapshot.stacks[0].fingerprint
    )


def test_committed_type_only_change_changes_fingerprint_and_is_clean(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    compose_path = "stacks/media/example/compose.yaml"
    compose = repository / compose_path
    compose.write_bytes(b"compose-source.yaml")
    git(repository, "add", compose_path)
    git(repository, "commit", "-m", "use same bytes for regular compose input")
    regular_commit = git(repository, "rev-parse", "HEAD")
    regular = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState(
            "main", regular_commit
        ),
    )
    compose.unlink()
    compose.symlink_to("compose-source.yaml")
    git(repository, "add", compose_path)
    git(repository, "commit", "-m", "change compose input to symlink")
    symlink_commit = git(repository, "rev-parse", "HEAD")

    symlink = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState(
            "main", symlink_commit
        ),
    )

    assert regular.snapshot is not None
    assert symlink.snapshot is not None
    assert symlink.snapshot.stacks[0].complete is True
    assert (
        regular.snapshot.stacks[0].fingerprint
        != symlink.snapshot.stacks[0].fingerprint
    )


def test_committed_compose_tree_changes_fingerprint_and_is_clean(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    compose_path = "stacks/media/example/compose.yaml"
    compose = repository / compose_path
    compose.unlink()
    git(repository, "add", compose_path)
    git(repository, "commit", "-m", "remove compose input")
    without_compose_commit = git(repository, "rev-parse", "HEAD")
    without_compose = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState(
            "main", without_compose_commit
        ),
    )
    compose.mkdir()
    (compose / "fragment.yaml").write_text("services: {}\n", encoding="utf-8")
    git(repository, "add", compose_path)
    git(repository, "commit", "-m", "use a tree at the compose input path")
    tree_commit = git(repository, "rev-parse", "HEAD")

    tree = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", tree_commit),
    )

    assert without_compose.snapshot is not None
    assert tree.snapshot is not None
    tree_stack = tree.snapshot.stacks[0]
    assert compose_path in tree_stack.relevant_inputs
    assert tree_stack.complete is True
    assert tree_stack.changed_inputs == ()
    assert (
        tree_stack.fingerprint
        != without_compose.snapshot.stacks[0].fingerprint
    )


def test_matching_compose_gitlink_worktree_is_complete_without_mutation(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    compose, compose_path, first_commit, _second_commit = create_compose_gitlink(
        repository
    )
    commit = git(repository, "rev-parse", "HEAD")
    before_status = git(
        repository, "status", "--porcelain=v1", "--untracked-files=all"
    )
    before_index = git(repository, "ls-files", "--stage", "--", compose_path)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    stack = result.snapshot.stacks[0]
    assert stack.complete is True
    assert stack.changed_inputs == ()
    assert compose_path in stack.relevant_inputs
    assert (
        git(repository, "status", "--porcelain=v1", "--untracked-files=all")
        == before_status
    )
    assert git(repository, "ls-files", "--stage", "--", compose_path) == before_index
    assert git(compose, "rev-parse", "HEAD") == first_commit


def test_mismatched_compose_gitlink_worktree_is_incomplete_without_mutation(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    compose, compose_path, _first_commit, second_commit = create_compose_gitlink(
        repository
    )
    commit = git(repository, "rev-parse", "HEAD")
    git(compose, "checkout", "--detach", second_commit)
    before_status = git(
        repository, "status", "--porcelain=v1", "--untracked-files=all"
    )
    before_index = git(repository, "ls-files", "--stage", "--", compose_path)
    before_gitlink_head = git(compose, "rev-parse", "HEAD")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    stack = result.snapshot.stacks[0]
    assert stack.complete is False
    assert stack.changed_inputs == (compose_path,)
    assert (
        git(repository, "status", "--porcelain=v1", "--untracked-files=all")
        == before_status
    )
    assert git(repository, "ls-files", "--stage", "--", compose_path) == before_index
    assert git(compose, "rev-parse", "HEAD") == before_gitlink_head


@pytest.mark.parametrize("deviation", ["worktree", "index", "untracked"])
def test_dirty_matching_compose_gitlink_is_incomplete_without_mutation(
    tmp_path: Path, deviation: str
) -> None:
    repository, _ = create_repository(tmp_path)
    compose, compose_path, pinned_commit, _second_commit = create_compose_gitlink(
        repository
    )
    commit = git(repository, "rev-parse", "HEAD")
    nested_input = compose / "compose.yaml"
    if deviation == "worktree":
        nested_input.write_text("services:\n  local: {}\n", encoding="utf-8")
    elif deviation == "index":
        nested_input.write_text("services:\n  staged: {}\n", encoding="utf-8")
        git(compose, "add", "compose.yaml")
        nested_input.write_text("services: {}\n", encoding="utf-8")
    else:
        (compose / "local.yaml").write_text("services: {}\n", encoding="utf-8")

    before_parent_status = git(
        repository, "status", "--porcelain=v1", "--untracked-files=all"
    )
    before_parent_index = git(repository, "ls-files", "--stage", "--", compose_path)
    before_nested_status = git(
        compose, "status", "--porcelain=v1", "--untracked-files=all"
    )
    before_nested_index = git(compose, "ls-files", "--stage")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert before_parent_status == f"M {compose_path}"
    assert result.snapshot is not None
    stack = result.snapshot.stacks[0]
    assert stack.complete is False
    assert stack.changed_inputs == (compose_path,)
    assert git(repository, "rev-parse", "HEAD") == commit
    assert git(compose, "rev-parse", "HEAD") == pinned_commit
    assert (
        git(repository, "status", "--porcelain=v1", "--untracked-files=all")
        == before_parent_status
    )
    assert (
        git(repository, "ls-files", "--stage", "--", compose_path)
        == before_parent_index
    )
    assert (
        git(compose, "status", "--porcelain=v1", "--untracked-files=all")
        == before_nested_status
    )
    assert git(compose, "ls-files", "--stage") == before_nested_index


@pytest.mark.parametrize("deviation", ["worktree-content", "index-content", "type"])
def test_local_compose_tree_deviation_makes_stack_incomplete(
    tmp_path: Path, deviation: str
) -> None:
    repository, _ = create_repository(tmp_path)
    compose_path = "stacks/media/example/compose.yaml"
    compose = repository / compose_path
    compose.unlink()
    compose.mkdir()
    fragment = compose / "fragment.yaml"
    fragment.write_text("services: {}\n", encoding="utf-8")
    git(repository, "add", compose_path)
    git(repository, "commit", "-m", "use a tree at the compose input path")
    commit = git(repository, "rev-parse", "HEAD")

    if deviation == "worktree-content":
        fragment.write_text("services:\n  changed: {}\n", encoding="utf-8")
    elif deviation == "index-content":
        fragment.write_text("services:\n  staged: {}\n", encoding="utf-8")
        git(repository, "add", compose_path)
        fragment.write_text("services: {}\n", encoding="utf-8")
    else:
        fragment.unlink()
        compose.rmdir()
        compose.write_text("services: {}\n", encoding="utf-8")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    tree_stack = result.snapshot.stacks[0]
    assert tree_stack.complete is False
    assert tree_stack.changed_inputs == (compose_path,)


@pytest.mark.parametrize(
    "relevant_path",
    [
        "stacks/media/example/compose.yaml",
        "stacks/media/example/stack.yaml",
        "stacks/media/example/docs/upgrade.md",
    ],
)
def test_index_only_content_change_makes_stack_incomplete_without_mutating_index(
    tmp_path: Path, relevant_path: str
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    (stack / "stack.yaml").write_text(
        """\
updates:
  mode: images
  track: stable
  procedure:
    mode: assisted
    runbook: docs/upgrade.md
""",
        encoding="utf-8",
    )
    (stack / "docs").mkdir()
    (stack / "docs/upgrade.md").write_text("# Upgrade\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add assisted runbook")
    commit = git(repository, "rev-parse", "HEAD")
    relevant = repository / relevant_path
    head_content = relevant.read_bytes()
    relevant.write_bytes(b"staged content\n")
    git(repository, "add", relevant_path)
    relevant.write_bytes(head_content)
    before_status = git(repository, "status", "--porcelain=v1", "--", relevant_path)
    before_index = git(repository, "ls-files", "--stage", "-v", "--", relevant_path)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert before_status.startswith("MM ")
    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.complete is False
    assert snapshot.changed_inputs == (relevant_path,)
    assert (
        git(repository, "status", "--porcelain=v1", "--", relevant_path)
        == before_status
    )
    assert (
        git(repository, "ls-files", "--stage", "-v", "--", relevant_path)
        == before_index
    )


def test_index_only_added_compose_input_is_relevant_and_incomplete(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)
    override_path = "stacks/media/example/compose.override.yaml"
    override = repository / override_path
    override.write_text(
        "services:\n  worker:\n    image: example/worker:1\n", encoding="utf-8"
    )
    git(repository, "add", override_path)
    override.unlink()
    before_index = git(repository, "ls-files", "--stage", "--", override_path)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert override_path in snapshot.relevant_inputs
    assert override_path in snapshot.changed_inputs
    assert snapshot.complete is False
    assert git(repository, "ls-files", "--stage", "--", override_path) == before_index


def test_checked_in_policy_selects_runbook_relevance_despite_dirty_policy(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    (stack / "stack.yaml").write_text(
        """\
updates:
  mode: images
  track: stable
  procedure:
    mode: assisted
    runbook: docs/upgrade.md#steps
""",
        encoding="utf-8",
    )
    (stack / "docs").mkdir()
    checked_in_runbook = stack / "docs/upgrade.md"
    checked_in_runbook.write_text("# Steps\n\nUpgrade.\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "assisted policy")
    commit = git(repository, "rev-parse", "HEAD")
    (stack / "stack.yaml").write_text(
        """\
updates:
  mode: images
  track: stable
  procedure:
    mode: assisted
    runbook: docs/local.md
""",
        encoding="utf-8",
    )
    checked_in_runbook.write_text("locally edited\n", encoding="utf-8")
    (stack / "docs/local.md").write_text("local only\n", encoding="utf-8")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/docs/upgrade.md",
        "stacks/media/example/stack.yaml",
    )
    assert snapshot.changed_inputs == (
        "stacks/media/example/docs/upgrade.md",
        "stacks/media/example/stack.yaml",
    )
    assert snapshot.complete is False


def test_binary_checked_in_policy_has_deterministic_snapshot_without_runbook(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    policy_path = "stacks/media/example/stack.yaml"
    runbook_path = "stacks/media/example/docs/upgrade.md"
    (stack / "stack.yaml").write_bytes(
        b"\xffupdates:\n  procedure:\n    mode: assisted\n    runbook: docs/upgrade.md\n"
    )
    (stack / "docs").mkdir()
    (stack / "docs/upgrade.md").write_text("# Upgrade\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add binary policy")
    commit = git(repository, "rev-parse", "HEAD")
    before_status = git(
        repository, "status", "--porcelain=v1", "--untracked-files=all"
    )
    before_index = git(repository, "ls-files", "--stage", "--", policy_path)
    reader = lambda owner, name: GitHubRepositoryState("main", commit)

    first = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )
    second = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )

    assert first.errors == ()
    assert first.snapshot is not None
    assert second.snapshot is not None
    snapshot = first.snapshot.stacks[0]
    assert snapshot.complete is True
    assert snapshot.changed_inputs == ()
    assert snapshot.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        policy_path,
    )
    assert runbook_path not in snapshot.relevant_inputs
    assert len(snapshot.fingerprint) == 64
    assert second.snapshot.stacks == first.snapshot.stacks
    assert (
        git(repository, "status", "--porcelain=v1", "--untracked-files=all")
        == before_status
    )
    assert git(repository, "ls-files", "--stage", "--", policy_path) == before_index


def test_checked_in_policy_with_nul_runbook_has_snapshot_without_runbook(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    policy_path = "stacks/media/example/stack.yaml"
    (stack / "stack.yaml").write_text(
        """\
updates:
  mode: images
  track: stable
  procedure:
    mode: assisted
    runbook: "bad\\0path"
""",
        encoding="utf-8",
    )
    git(repository, "add", policy_path)
    git(repository, "commit", "-m", "add malformed assisted runbook")
    commit = git(repository, "rev-parse", "HEAD")
    before_status = git(
        repository, "status", "--porcelain=v1", "--untracked-files=all"
    )
    before_index = git(repository, "ls-files", "--stage", "--", policy_path)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.complete is True
    assert snapshot.changed_inputs == ()
    assert snapshot.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        policy_path,
    )
    assert len(snapshot.fingerprint) == 64
    assert (
        git(repository, "status", "--porcelain=v1", "--untracked-files=all")
        == before_status
    )
    assert git(repository, "ls-files", "--stage", "--", policy_path) == before_index


def test_checked_in_policy_with_surrogate_runbook_has_snapshot_without_runbook(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    policy_path = "stacks/media/example/stack.yaml"
    (repository / policy_path).write_text(
        """\
updates:
  mode: images
  track: stable
  procedure:
    mode: assisted
    runbook: "\\uD800"
""",
        encoding="utf-8",
    )
    git(repository, "add", policy_path)
    git(repository, "commit", "-m", "add unrepresentable assisted runbook")
    commit = git(repository, "rev-parse", "HEAD")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.complete is True
    assert snapshot.changed_inputs == ()
    assert snapshot.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        policy_path,
    )
    assert len(snapshot.fingerprint) == 64


def test_locally_added_runbook_named_by_checked_in_policy_is_relevant(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    (stack / "stack.yaml").write_text(
        """\
updates:
  mode: images
  track: stable
  procedure:
    mode: assisted
    runbook: docs/upgrade.md
""",
        encoding="utf-8",
    )
    git(repository, "add", "stacks/media/example/stack.yaml")
    git(repository, "commit", "-m", "policy awaiting runbook")
    commit = git(repository, "rev-parse", "HEAD")
    (stack / "docs").mkdir()
    (stack / "docs/upgrade.md").write_text("# Upgrade\n", encoding="utf-8")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    stack_snapshot = result.snapshot.stacks[0]
    assert "stacks/media/example/docs/upgrade.md" in stack_snapshot.relevant_inputs
    assert stack_snapshot.changed_inputs == ("stacks/media/example/docs/upgrade.md",)
    assert stack_snapshot.complete is False


def test_supported_locally_added_and_deleted_compose_inputs_are_relevant(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    (stack / "compose.yaml").unlink()
    (stack / "compose.override.yml").write_text(
        "services:\n  worker:\n    image: example/worker:1\n", encoding="utf-8"
    )

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    stack_snapshot = result.snapshot.stacks[0]
    assert stack_snapshot.relevant_inputs == (
        "stacks/media/example/compose.override.yml",
        "stacks/media/example/compose.yaml",
        "stacks/media/example/stack.yaml",
    )
    assert stack_snapshot.changed_inputs == (
        "stacks/media/example/compose.override.yml",
        "stacks/media/example/compose.yaml",
    )


def test_ignored_locally_added_supported_compose_input_makes_stack_incomplete(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)
    override_path = "stacks/media/example/compose.override.yaml"
    (repository / ".git/info/exclude").write_text(
        f"/{override_path}\n", encoding="utf-8"
    )
    (repository / override_path).write_text(
        "services:\n  worker:\n    image: example/worker:1\n", encoding="utf-8"
    )

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert git(repository, "status", "--porcelain=v1", "--", override_path) == ""
    assert result.snapshot is not None
    stack_snapshot = result.snapshot.stacks[0]
    assert override_path in stack_snapshot.relevant_inputs
    assert stack_snapshot.changed_inputs == (override_path,)
    assert stack_snapshot.complete is False


def test_assume_unchanged_policy_modification_makes_stack_incomplete(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)
    policy_path = "stacks/media/example/stack.yaml"
    git(repository, "update-index", "--assume-unchanged", policy_path)
    before_index_entry = git(repository, "ls-files", "-v", "--", policy_path)
    (repository / policy_path).write_text(
        "updates:\n  mode: images\n  track: edge\n", encoding="utf-8"
    )

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert git(repository, "status", "--porcelain=v1", "--", policy_path) == ""
    assert result.snapshot is not None
    stack_snapshot = result.snapshot.stacks[0]
    assert stack_snapshot.changed_inputs == (policy_path,)
    assert stack_snapshot.complete is False
    assert git(repository, "ls-files", "-v", "--", policy_path) == before_index_entry


@pytest.mark.parametrize(
    ("origin", "code"),
    [
        (None, "missing-origin"),
        ("https://gitlab.com/example/homelab.git", "invalid-origin"),
        ("https://github.com/example", "invalid-origin"),
    ],
)
def test_missing_or_invalid_github_origin_fails_structurally(
    tmp_path: Path, origin: str | None, code: str
) -> None:
    repository, commit = create_repository(tmp_path)
    git(repository, "remote", "remove", "origin")
    if origin is not None:
        git(repository, "remote", "add", "origin", origin)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is None
    assert [error.code for error in result.errors] == [code]


def test_github_read_failure_is_structured(tmp_path: Path) -> None:
    repository, _ = create_repository(tmp_path)

    def unavailable(owner: str, name: str) -> GitHubRepositoryState:
        raise GitHubReadError("GitHub repository could not be read")

    result = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=unavailable
    )

    assert result.snapshot is None
    assert [(error.code, error.path, error.message) for error in result.errors] == [
        (
            "github-read-failed",
            "repository.github",
            "GitHub repository could not be read",
        )
    ]


@pytest.mark.parametrize(
    ("state", "code", "path"),
    [
        (None, "invalid-github-response", "repository.github"),
        (GitHubRepositoryState("", "a" * 40), "missing-default-branch", "repository.default_branch"),
        (GitHubRepositoryState("main", ""), "missing-default-branch-commit", "repository.commit"),
        (GitHubRepositoryState("main", "not-a-commit"), "invalid-default-branch-commit", "repository.commit"),
    ],
)
def test_invalid_default_branch_resolution_is_structured(
    tmp_path: Path, state: GitHubRepositoryState | None, code: str, path: str
) -> None:
    repository, _ = create_repository(tmp_path)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: state,
    )

    assert result.snapshot is None
    assert [(error.code, error.path) for error in result.errors] == [(code, path)]


@pytest.mark.parametrize(
    "default_branch",
    ["main\0unsafe", "main\nunsafe", "main\ud800unsafe", "main..unsafe"],
)
def test_unsafe_default_branch_fails_with_safe_structured_error(
    tmp_path: Path, default_branch: str
) -> None:
    repository, commit = create_repository(tmp_path)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState(
            default_branch, commit
        ),
    )

    assert result.snapshot is None
    assert [error.as_dict() for error in result.errors] == [
        {
            "code": "invalid-default-branch",
            "message": "GitHub returned an invalid default branch",
            "path": "repository.default_branch",
        }
    ]


def test_valid_slash_default_branch_is_preserved_in_snapshot(tmp_path: Path) -> None:
    repository, commit = create_repository(tmp_path)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState(
            "release/stable", commit
        ),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    assert result.snapshot.default_branch == "release/stable"


def test_snapshot_is_deterministic_and_leaves_repository_and_worktree_unchanged(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)
    compose = repository / "stacks/media/example/compose.yaml"
    before_head = git(repository, "rev-parse", "HEAD")
    before_branch = git(repository, "branch", "--show-current")
    before_status = git(repository, "status", "--porcelain=v1", "--untracked-files=all")
    before_content = compose.read_bytes()
    first = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )
    os.utime(compose, (1_000_000_000, 1_000_000_000))

    second = build_repository_snapshot(
        repository,
        ["stacks/media/example", "stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert first.snapshot is not None
    assert second.snapshot is not None
    assert second.snapshot.stacks == first.snapshot.stacks
    assert git(repository, "rev-parse", "HEAD") == before_head
    assert git(repository, "branch", "--show-current") == before_branch
    assert git(repository, "status", "--porcelain=v1", "--untracked-files=all") == before_status
    assert compose.read_bytes() == before_content


@pytest.mark.parametrize(
    ("selected", "code"),
    [
        ("media/example", "invalid-stack-identity"),
        ("stacks/media/missing", "missing-stack-inputs"),
    ],
)
def test_non_repo_managed_stack_selection_fails_structurally(
    tmp_path: Path, selected: str, code: str
) -> None:
    repository, commit = create_repository(tmp_path)

    result = build_repository_snapshot(
        repository,
        [selected],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is None
    assert [error.code for error in result.errors] == [code]


@pytest.mark.parametrize(
    "selected", ["stacks/host/bad\0name", "stacks/host/bad\ud800name"]
)
def test_unrepresentable_selected_stack_identity_fails_structurally(
    tmp_path: Path, selected: str
) -> None:
    repository, commit = create_repository(tmp_path)

    result = build_repository_snapshot(
        repository,
        [selected],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is None
    assert [error.as_dict() for error in result.errors] == [
        {
            "code": "invalid-stack-identity",
            "message": "selected stack identity must be stacks/<host>/<stack>",
            "path": "stack.identity",
        }
    ]


@pytest.mark.parametrize(
    "selected_stacks",
    [[None], ["stacks/media/example", None]],
)
def test_non_string_selected_stack_identity_fails_structurally(
    tmp_path: Path, selected_stacks: list[object]
) -> None:
    repository, commit = create_repository(tmp_path)

    result = build_repository_snapshot(
        repository,
        selected_stacks,  # type: ignore[arg-type]
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is None
    assert [error.as_dict() for error in result.errors] == [
        {
            "code": "invalid-stack-identity",
            "message": "selected stack identity must be stacks/<host>/<stack>",
            "path": "stack.identity",
        }
    ]


def test_missing_checked_in_blob_fails_snapshot_without_partial_result(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)
    policy_path = "stacks/media/example/stack.yaml"
    object_id = git(repository, "rev-parse", f"HEAD:{policy_path}")
    object_path = repository / ".git/objects" / object_id[:2] / object_id[2:]
    object_path.unlink()

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is None
    assert [error.as_dict() for error in result.errors] == [
        {
            "code": "repository-input-read-failed",
            "message": "repository input could not be read",
            "path": policy_path,
        }
    ]


def test_staged_input_with_unreadable_checked_in_blob_fails_without_partial_snapshot(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)
    compose_path = "stacks/media/example/compose.yaml"
    object_id = git(repository, "rev-parse", f"HEAD:{compose_path}")
    (repository / compose_path).write_text("services: {}\n", encoding="utf-8")
    git(repository, "add", compose_path)
    object_path = repository / ".git/objects" / object_id[:2] / object_id[2:]
    object_path.unlink()

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is None
    assert [error.as_dict() for error in result.errors] == [
        {
            "code": "repository-input-read-failed",
            "message": "repository input could not be read",
            "path": compose_path,
        }
    ]
