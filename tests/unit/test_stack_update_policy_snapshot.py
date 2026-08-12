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


def test_checkout_not_at_remote_default_branch_commit_fails_before_stack_discovery(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)
    git(repository, "checkout", "-b", "feature")
    (repository / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(repository, "add", "feature.txt")
    git(repository, "commit", "-m", "feature")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/does-not-exist"],
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
