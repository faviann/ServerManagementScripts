import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from stack_update_policy import (  # noqa: E402
    GitHubReadError,
    GitHubRepositoryState,
    StackInputSnapshot,
    build_repository_snapshot,
    read_github_repository,
)
from stack_update_policy import snapshot as snapshot_module  # noqa: E402


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


def create_literal_pathspec_collision_repository(
    tmp_path: Path, selected_name: str, sibling_name: str
) -> tuple[Path, str, str, str]:
    repository, _ = create_repository(tmp_path)
    original = repository / "stacks/media/example"
    selected = repository / "stacks/media" / selected_name
    original.rename(selected)
    (selected / "stack.yaml").write_text(
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
    (selected / "docs").mkdir()
    (selected / "docs/upgrade.md").write_text("# Upgrade\n", encoding="utf-8")
    sibling = repository / "stacks/media" / sibling_name
    sibling.mkdir()
    for relative_path in ("compose.yaml", "stack.yaml", "docs/upgrade.md"):
        source = selected / relative_path
        target = sibling / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add literal pathspec collision stacks")
    return (
        repository,
        git(repository, "rev-parse", "HEAD"),
        f"stacks/media/{selected_name}",
        f"stacks/media/{sibling_name}",
    )


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


def create_nested_compose_gitlink(repository: Path) -> tuple[Path, str, str, str]:
    compose_path = "stacks/media/example/compose.yaml"
    compose = repository / compose_path
    compose.unlink()
    compose.mkdir()
    (compose / "fragment.yaml").write_text("services: {}\n", encoding="utf-8")
    nested = compose / "vendor/plugin"
    nested.mkdir(parents=True)
    git(nested, "init", "-b", "main")
    git(nested, "config", "user.name", "Snapshot Test")
    git(nested, "config", "user.email", "snapshot@example.invalid")
    (nested / "plugin.yaml").write_text("version: 1\n", encoding="utf-8")
    git(nested, "add", "plugin.yaml")
    git(nested, "commit", "-m", "first plugin input")
    first_commit = git(nested, "rev-parse", "HEAD")
    (nested / "plugin.yaml").write_text("version: 2\n", encoding="utf-8")
    git(nested, "add", "plugin.yaml")
    git(nested, "commit", "-m", "second plugin input")
    second_commit = git(nested, "rev-parse", "HEAD")
    git(nested, "checkout", "--detach", first_commit)
    git(repository, "add", compose_path)
    git(repository, "commit", "-m", "use nested gitlink in compose tree")
    return nested, compose_path, first_commit, second_commit


@pytest.mark.parametrize("branch", ["release/stable", "a" * 40])
def test_github_reader_reads_one_coherent_default_branch_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    request_log = tmp_path / "requests.log"
    tip = "b" * 40
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        r"""#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_GH_REQUEST_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
print(json.dumps({
    "data": {
        "repository": {
            "defaultBranchRef": {
                "name": os.environ["FAKE_GH_BRANCH"],
                "target": {"oid": os.environ["FAKE_GH_TIP"]},
            }
        }
    }
}))
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_REQUEST_LOG", str(request_log))
    monkeypatch.setenv("FAKE_GH_BRANCH", branch)
    monkeypatch.setenv("FAKE_GH_TIP", tip)

    state = read_github_repository("example", "homelab")

    assert state == GitHubRepositoryState(branch, tip)
    requests = request_log.read_text(encoding="utf-8").splitlines()
    assert len(requests) == 1
    arguments = json.loads(requests[0])
    assert arguments[:2] == ["api", "graphql"]
    assert "-F" in arguments
    assert "owner=example" in arguments
    assert "name=homelab" in arguments
    query_arguments = [
        argument for argument in arguments if argument.startswith("query=")
    ]
    assert len(query_arguments) == 1
    assert "$owner" in query_arguments[0]
    assert "$name" in query_arguments[0]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": {}, "unexpected": True},
        {"data": {"repository": {"defaultBranchRef": None}}},
        {
            "data": {
                "repository": {
                    "defaultBranchRef": {"name": "main", "target": {}}
                }
            }
        },
        {
            "data": {
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": 123},
                    }
                }
            }
        },
    ],
)
def test_github_reader_fails_safe_for_malformed_coherent_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    request_log = tmp_path / "requests.log"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        r"""#!/usr/bin/env python3
import os
import sys

with open(os.environ["FAKE_GH_REQUEST_LOG"], "a", encoding="utf-8") as log:
    log.write("called\n")
sys.stdout.write(os.environ["FAKE_GH_PAYLOAD"])
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_REQUEST_LOG", str(request_log))
    monkeypatch.setenv("FAKE_GH_PAYLOAD", json.dumps(payload))

    with pytest.raises(GitHubReadError, match="could not be read"):
        read_github_repository("example", "homelab")

    assert request_log.read_text(encoding="utf-8").splitlines() == ["called"]


def test_github_reader_fails_safe_for_non_utf8_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import os

os.write(1, b"\\xffunsafe stdout")
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(GitHubReadError) as raised:
        read_github_repository("example", "homelab")

    assert str(raised.value) == "GitHub repository could not be read"
    assert "unsafe" not in str(raised.value)


def test_github_reader_times_out_safely_and_terminates_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    process_id = tmp_path / "gh.pid"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        r"""#!/usr/bin/env python3
import os
import sys
import time

with open(os.environ["FAKE_GH_PID"], "w", encoding="utf-8") as pid_file:
    pid_file.write(str(os.getpid()))
sys.stdout.write("unsafe stdout")
sys.stderr.write("unsafe stderr")
sys.stdout.flush()
sys.stderr.flush()
time.sleep(30)
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_PID", str(process_id))
    monkeypatch.setattr(snapshot_module, "_GITHUB_READ_TIMEOUT_SECONDS", 0.05)

    started = time.monotonic()
    with pytest.raises(GitHubReadError) as raised:
        read_github_repository("example", "homelab")
    elapsed = time.monotonic() - started

    assert str(raised.value) == "GitHub repository could not be read"
    assert "unsafe" not in str(raised.value)
    assert elapsed < 2
    pid = int(process_id.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


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


@pytest.mark.parametrize(
    ("selected_name", "sibling_name"),
    [("app*", "appx"), ("app?", "appx"), ("app[ab]", "appa")],
)
@pytest.mark.parametrize("relative_path", ["compose.yaml", "docs/upgrade.md"])
def test_staged_pathspec_collision_does_not_change_selected_stack_snapshot(
    tmp_path: Path,
    selected_name: str,
    sibling_name: str,
    relative_path: str,
) -> None:
    repository, commit, selected_identity, sibling_identity = (
        create_literal_pathspec_collision_repository(
            tmp_path, selected_name, sibling_name
        )
    )
    sibling_path = f"{sibling_identity}/{relative_path}"
    (repository / sibling_path).write_text("staged sibling change\n", encoding="utf-8")
    git(repository, "--literal-pathspecs", "add", "--", sibling_path)

    result = build_repository_snapshot(
        repository,
        [selected_identity],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.identity == selected_identity
    assert snapshot.complete is True
    assert snapshot.changed_inputs == ()


@pytest.mark.parametrize(
    ("selected_name", "sibling_name"),
    [("app*", "appx"), ("app?", "appx"), ("app[ab]", "appa")],
)
def test_staged_literal_pathspec_input_change_is_detected(
    tmp_path: Path, selected_name: str, sibling_name: str
) -> None:
    repository, commit, selected_identity, _ = (
        create_literal_pathspec_collision_repository(
            tmp_path, selected_name, sibling_name
        )
    )
    selected_path = f"{selected_identity}/compose.yaml"
    selected_input = repository / selected_path
    head_content = selected_input.read_bytes()
    selected_input.write_text("staged selected change\n", encoding="utf-8")
    git(repository, "--literal-pathspecs", "add", "--", selected_path)
    selected_input.write_bytes(head_content)

    result = build_repository_snapshot(
        repository,
        [selected_identity],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.complete is False
    assert snapshot.changed_inputs == (selected_path,)


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
    (compose.parent / "compose-source.yaml").write_bytes(b"compose-source.yaml")
    git(
        repository,
        "add",
        compose_path,
        "stacks/media/example/compose-source.yaml",
    )
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


@pytest.mark.parametrize(
    ("deviation", "expected_complete"),
    [("clean", True), ("mismatched", False), ("dirty", False)],
)
def test_nested_compose_gitlink_is_compared_atomically_without_mutation(
    tmp_path: Path, deviation: str, expected_complete: bool
) -> None:
    repository, _ = create_repository(tmp_path)
    nested, compose_path, pinned_commit, second_commit = (
        create_nested_compose_gitlink(repository)
    )
    commit = git(repository, "rev-parse", "HEAD")
    if deviation == "mismatched":
        git(nested, "checkout", "--detach", second_commit)
    elif deviation == "dirty":
        (nested / "plugin.yaml").write_text("version: local\n", encoding="utf-8")

    before_parent_status = git(
        repository, "status", "--porcelain=v1", "--untracked-files=all"
    )
    before_parent_index = git(repository, "ls-files", "--stage", "--", compose_path)
    before_nested_head = git(nested, "rev-parse", "HEAD")
    before_nested_status = git(
        nested, "status", "--porcelain=v1", "--untracked-files=all"
    )

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    stack = result.snapshot.stacks[0]
    assert stack.complete is expected_complete
    assert stack.changed_inputs == (() if expected_complete else (compose_path,))
    assert git(repository, "rev-parse", "HEAD") == commit
    assert git(nested, "rev-parse", "HEAD") == before_nested_head
    assert (
        git(repository, "status", "--porcelain=v1", "--untracked-files=all")
        == before_parent_status
    )
    assert (
        git(repository, "ls-files", "--stage", "--", compose_path)
        == before_parent_index
    )
    assert (
        git(nested, "status", "--porcelain=v1", "--untracked-files=all")
        == before_nested_status
    )
    if deviation == "clean":
        assert before_nested_head == pinned_commit


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


def test_checked_in_symlinked_runbook_directory_binds_link_and_target(
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
    (stack / "runbooks").mkdir()
    (stack / "runbooks/upgrade.md").write_text("# Upgrade\n", encoding="utf-8")
    (stack / "docs").symlink_to("runbooks", target_is_directory=True)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add symlinked runbook directory")
    commit = git(repository, "rev-parse", "HEAD")
    before_status = git(repository, "status", "--porcelain=v1", "--untracked-files=all")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )
    repeated = build_repository_snapshot(
        repository,
        ["stacks/media/example", "stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    assert repeated.snapshot == result.snapshot
    assert (
        git(repository, "status", "--porcelain=v1", "--untracked-files=all")
        == before_status
    )
    snapshot = result.snapshot.stacks[0]
    assert snapshot.complete is True
    assert snapshot.changed_inputs == ()
    assert snapshot.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/docs",
        "stacks/media/example/runbooks/upgrade.md",
        "stacks/media/example/stack.yaml",
    )


def test_checked_in_runbook_symlink_fingerprint_binds_link_and_target(
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
    runbook: guide.md
""",
        encoding="utf-8",
    )
    (stack / "runbooks").mkdir()
    target = stack / "runbooks/upgrade.md"
    target.write_text("# Upgrade\n", encoding="utf-8")
    alternate = stack / "runbooks/alternate.md"
    alternate.write_bytes(target.read_bytes())
    link = stack / "guide.md"
    link.symlink_to("runbooks/upgrade.md")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add symlinked runbook")

    def snapshot_at_head() -> StackInputSnapshot:
        commit = git(repository, "rev-parse", "HEAD")
        result = build_repository_snapshot(
            repository,
            ["stacks/media/example"],
            github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
        )
        assert result.snapshot is not None
        return result.snapshot.stacks[0]

    original = snapshot_at_head()
    target.write_text("# Changed upgrade\n", encoding="utf-8")
    git(repository, "add", "stacks/media/example/runbooks/upgrade.md")
    git(repository, "commit", "-m", "change runbook target")
    changed_target = snapshot_at_head()
    link.unlink()
    link.symlink_to("runbooks/alternate.md")
    git(repository, "add", "stacks/media/example/guide.md")
    git(repository, "commit", "-m", "change runbook link")
    changed_link = snapshot_at_head()

    assert (
        len(
            {
                original.fingerprint,
                changed_target.fingerprint,
                changed_link.fingerprint,
            }
        )
        == 3
    )


@pytest.mark.parametrize(
    "canonical_name",
    [
        "compose.yaml",
        "compose.yml",
        "compose.override.yaml",
        "compose.override.yml",
    ],
)
def test_checked_in_compose_symlink_binds_link_and_target(
    tmp_path: Path, canonical_name: str
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    original = stack / "compose.yaml"
    if original.exists() or original.is_symlink():
        original.unlink()
    fragments = stack / "fragments"
    fragments.mkdir()
    target = fragments / "services.yaml"
    target.write_text("services:\n  app:\n    image: example/app:1\n", encoding="utf-8")
    (stack / canonical_name).symlink_to("fragments/services.yaml")
    git(repository, "add", ".")
    git(repository, "commit", "-m", f"symlink {canonical_name}")
    commit = git(repository, "rev-parse", "HEAD")
    reader = lambda owner, name: GitHubRepositoryState("main", commit)

    clean = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )
    target.write_text("services:\n  changed: {}\n", encoding="utf-8")
    dirty = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )

    assert clean.snapshot is not None
    assert dirty.snapshot is not None
    canonical_path = f"stacks/media/example/{canonical_name}"
    target_path = "stacks/media/example/fragments/services.yaml"
    assert clean.snapshot.stacks[0].relevant_inputs == tuple(
        sorted(
            {
                canonical_path,
                target_path,
                "stacks/media/example/stack.yaml",
            }
        )
    )
    assert clean.snapshot.stacks[0].complete is True
    assert dirty.snapshot.stacks[0].changed_inputs == (target_path,)
    assert dirty.snapshot.stacks[0].complete is False


@pytest.mark.parametrize(
    "canonical_name",
    [
        "stack.yaml",
        "compose.yaml",
        "compose.yml",
        "compose.override.yaml",
        "compose.override.yml",
    ],
)
def test_checked_in_canonical_symlink_with_missing_target_is_incomplete_and_bound(
    tmp_path: Path, canonical_name: str
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    canonical = stack / canonical_name
    canonical.unlink(missing_ok=True)
    canonical.symlink_to("fragments/missing.yaml")
    git(repository, "add", ".")
    git(repository, "commit", "-m", f"symlink {canonical_name} to missing input")

    def at_head() -> StackInputSnapshot:
        commit = git(repository, "rev-parse", "HEAD")
        result = build_repository_snapshot(
            repository,
            ["stacks/media/example"],
            github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
        )
        assert result.snapshot is not None
        return result.snapshot.stacks[0]

    original = at_head()
    repeated = at_head()
    canonical_path = f"stacks/media/example/{canonical_name}"
    missing_path = "stacks/media/example/fragments/missing.yaml"

    assert canonical_path in original.relevant_inputs
    assert missing_path in original.relevant_inputs
    assert original.changed_inputs == (missing_path,)
    assert original.complete is False
    assert repeated.fingerprint == original.fingerprint

    canonical.unlink()
    canonical.symlink_to("fragments/other-missing.yaml")
    git(repository, "add", canonical_path)
    git(repository, "commit", "-m", f"retarget {canonical_name}")

    assert at_head().fingerprint != original.fingerprint


@pytest.mark.parametrize("input_kind", ["compose", "policy", "runbook"])
@pytest.mark.parametrize(
    ("link_target", "terminal_suffix"),
    [
        ("resolved-input.yaml/impossible-child", "/impossible-child"),
        ("resolved-input.yaml/", ""),
    ],
)
def test_checked_in_input_symlink_cannot_traverse_through_a_file(
    tmp_path: Path,
    input_kind: str,
    link_target: str,
    terminal_suffix: str,
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    leaf = stack / "resolved-input.yaml"
    leaf.write_text("updates: {}\n", encoding="utf-8")
    if input_kind == "compose":
        link = stack / "compose.yaml"
    elif input_kind == "policy":
        link = stack / "stack.yaml"
    else:
        link = stack / "guide.yaml"
        (stack / "stack.yaml").write_text(
            """\
updates:
  mode: images
  track: stable
  procedure:
    mode: assisted
    runbook: guide.yaml
""",
            encoding="utf-8",
        )
    link.unlink(missing_ok=True)
    link.symlink_to(link_target)
    git(repository, "add", ".")
    git(repository, "commit", "-m", f"add impossible {input_kind} link")
    commit = git(repository, "rev-parse", "HEAD")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    link_path = link.relative_to(repository).as_posix()
    leaf_path = leaf.relative_to(repository).as_posix()
    assert snapshot.complete is False
    assert link_path in snapshot.relevant_inputs
    assert leaf_path in snapshot.relevant_inputs
    if terminal_suffix:
        terminal_path = f"{leaf_path}{terminal_suffix}"
        assert terminal_path in snapshot.relevant_inputs
        assert terminal_path in snapshot.changed_inputs
    else:
        assert snapshot.changed_inputs == ()


@pytest.mark.parametrize("input_kind", ["compose", "runbook"])
@pytest.mark.parametrize("obstacle_kind", ["missing", "file"])
def test_checked_in_input_symlink_cannot_cancel_an_unproven_parent_component(
    tmp_path: Path, input_kind: str, obstacle_kind: str
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    target = stack / "real.yaml"
    target.write_text("services: {}\n", encoding="utf-8")
    obstacle = stack / f"{obstacle_kind}-component"
    if obstacle_kind == "file":
        obstacle.write_text("not a directory\n", encoding="utf-8")
    if input_kind == "compose":
        link = stack / "compose.yaml"
    else:
        link = stack / "guide.yaml"
        (stack / "stack.yaml").write_text(
            """\
updates:
  mode: images
  track: stable
  procedure:
    mode: assisted
    runbook: guide.yaml
""",
            encoding="utf-8",
        )
    link.unlink(missing_ok=True)
    link.symlink_to(f"{obstacle.name}/../real.yaml")
    git(repository, "add", ".")
    git(repository, "commit", "-m", f"add impossible {input_kind} parent traversal")
    commit = git(repository, "rev-parse", "HEAD")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    link_path = link.relative_to(repository).as_posix()
    obstacle_path = obstacle.relative_to(repository).as_posix()
    assert snapshot.complete is False
    assert link_path in snapshot.relevant_inputs
    assert obstacle_path in snapshot.relevant_inputs
    assert target.relative_to(repository).as_posix() not in snapshot.relevant_inputs
    if obstacle_kind == "missing":
        assert obstacle_path in snapshot.changed_inputs
    else:
        assert obstacle_path not in snapshot.changed_inputs


@pytest.mark.parametrize("input_kind", ["compose", "runbook"])
@pytest.mark.parametrize("link_target", [".", "./", "fragments/.."])
def test_terminal_directory_symlink_binds_resolved_tree_and_descendant_changes(
    tmp_path: Path, input_kind: str, link_target: str
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    if link_target == "fragments/..":
        (stack / "fragments").mkdir()
        (stack / "fragments/proof.txt").write_text(
            "checked-in tree\n", encoding="utf-8"
        )
    if input_kind == "compose":
        link = stack / "compose.yaml"
    else:
        link = stack / "guide"
        (stack / "stack.yaml").write_text(
            """\
updates:
  mode: images
  track: stable
  procedure:
    mode: assisted
    runbook: guide
""",
            encoding="utf-8",
        )
    link.unlink(missing_ok=True)
    link.symlink_to(link_target, target_is_directory=True)
    descendant = stack / "bound-descendant.txt"
    descendant.write_text("original\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", f"bind {input_kind} to stack directory")

    def snapshot_at_head() -> StackInputSnapshot:
        commit = git(repository, "rev-parse", "HEAD")
        result = build_repository_snapshot(
            repository,
            ["stacks/media/example"],
            github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
        )
        assert result.errors == ()
        assert result.snapshot is not None
        return result.snapshot.stacks[0]

    clean = snapshot_at_head()
    descendant.write_text("local change\n", encoding="utf-8")
    dirty = snapshot_at_head()
    git(repository, "add", descendant.relative_to(repository).as_posix())
    git(repository, "commit", "-m", "change bound directory descendant")
    committed = snapshot_at_head()

    assert clean.complete is True
    assert "stacks/media/example" in clean.relevant_inputs
    assert dirty.complete is False
    assert "stacks/media/example" in dirty.changed_inputs
    assert committed.complete is True
    assert committed.fingerprint != clean.fingerprint


def test_checked_in_symlinked_policy_is_parsed_from_its_resolved_target(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    policy = stack / "stack.yaml"
    policy.unlink()
    metadata = stack / "metadata"
    metadata.mkdir()
    target = metadata / "policy.yaml"
    target.write_text(
        "updates:\n  procedure:\n    mode: assisted\n    runbook: docs/upgrade.md\n",
        encoding="utf-8",
    )
    policy.symlink_to("metadata/policy.yaml")
    (stack / "docs").mkdir()
    (stack / "docs/upgrade.md").write_text("# Upgrade\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "symlink policy")
    commit = git(repository, "rev-parse", "HEAD")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )
    target.write_text("updates: {}\n", encoding="utf-8")
    dirty = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    assert dirty.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.complete is True
    assert snapshot.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/docs/upgrade.md",
        "stacks/media/example/metadata/policy.yaml",
        "stacks/media/example/stack.yaml",
    )
    assert dirty.snapshot.stacks[0].relevant_inputs == snapshot.relevant_inputs
    assert dirty.snapshot.stacks[0].changed_inputs == (
        "stacks/media/example/metadata/policy.yaml",
    )
    assert dirty.snapshot.stacks[0].complete is False


def test_checked_in_compose_ancestor_chain_binds_every_link_and_final_target(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    (stack / "compose.yaml").unlink()
    (stack / "content").mkdir()
    target = stack / "content/final.yaml"
    target.write_text("services: {}\n", encoding="utf-8")
    (stack / "fragments").symlink_to("content", target_is_directory=True)
    (stack / "compose.yaml").symlink_to("fragments/final.yaml")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "compose link chain")
    commit = git(repository, "rev-parse", "HEAD")
    reader = lambda owner, name: GitHubRepositoryState("main", commit)

    clean = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )
    target.write_text("services:\n  changed: {}\n", encoding="utf-8")
    git(repository, "add", "stacks/media/example/content/final.yaml")
    staged = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )

    assert clean.snapshot is not None
    assert staged.snapshot is not None
    assert clean.snapshot.stacks[0].relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/content/final.yaml",
        "stacks/media/example/fragments",
        "stacks/media/example/stack.yaml",
    )
    assert staged.snapshot.stacks[0].changed_inputs == (
        "stacks/media/example/content/final.yaml",
    )


@pytest.mark.parametrize("staged", [False, True])
def test_checked_in_compose_link_dirtiness_makes_stack_incomplete(
    tmp_path: Path, staged: bool
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    compose_path = "stacks/media/example/compose.yaml"
    compose = repository / compose_path
    compose.unlink()
    (stack / "fragments").mkdir()
    (stack / "fragments/services.yaml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (stack / "fragments/alternate.yaml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    compose.symlink_to("fragments/services.yaml")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "symlink compose")
    commit = git(repository, "rev-parse", "HEAD")
    compose.unlink()
    compose.symlink_to("fragments/alternate.yaml")
    if staged:
        git(repository, "add", compose_path)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.complete is False
    assert snapshot.changed_inputs == (compose_path,)
    assert "stacks/media/example/fragments/services.yaml" in snapshot.relevant_inputs
    assert "stacks/media/example/fragments/alternate.yaml" not in snapshot.relevant_inputs


def test_committed_compose_symlink_target_changes_snapshot_fingerprint(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    compose = stack / "compose.yaml"
    compose.unlink()
    (stack / "fragments").mkdir()
    target = stack / "fragments/services.yaml"
    target.write_text("services: {}\n", encoding="utf-8")
    compose.symlink_to("fragments/services.yaml")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "symlink compose")

    def at_head() -> StackInputSnapshot:
        commit = git(repository, "rev-parse", "HEAD")
        result = build_repository_snapshot(
            repository,
            ["stacks/media/example"],
            github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
        )
        assert result.snapshot is not None
        return result.snapshot.stacks[0]

    original = at_head()
    target.write_text("services:\n  changed: {}\n", encoding="utf-8")
    git(repository, "add", "stacks/media/example/fragments/services.yaml")
    git(repository, "commit", "-m", "change compose target")

    assert at_head().fingerprint != original.fingerprint


def test_escaping_canonical_symlink_fails_closed_without_binding_external_target(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    compose = stack / "compose.yaml"
    compose.unlink()
    external = repository / "stacks/media/shared.yaml"
    external.write_text("services: {}\n", encoding="utf-8")
    compose.symlink_to("../../shared.yaml")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "escaping compose link")
    commit = git(repository, "rev-parse", "HEAD")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.complete is False
    assert snapshot.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/stack.yaml",
    )
    assert "stacks/media/shared.yaml" not in snapshot.relevant_inputs


def test_checked_in_runbook_symlink_chain_returns_each_link_and_final_target(
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
    (stack / "runbooks/content").mkdir(parents=True)
    (stack / "runbooks/content/final.md").write_text(
        "# Upgrade\n", encoding="utf-8"
    )
    (stack / "runbooks/upgrade.md").symlink_to("content/final.md")
    (stack / "docs").symlink_to("runbooks", target_is_directory=True)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add runbook symlink chain")
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
    assert snapshot.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/docs",
        "stacks/media/example/runbooks/content/final.md",
        "stacks/media/example/runbooks/upgrade.md",
        "stacks/media/example/stack.yaml",
    )


def test_checked_in_runbook_symlink_targets_normalize_within_selected_stack(
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
    (stack / "docs").mkdir()
    (stack / "runbooks").mkdir()
    (stack / "runbooks/content").mkdir()
    (stack / "runbooks/content/proof.txt").write_text(
        "checked-in tree\n", encoding="utf-8"
    )
    target = stack / "runbooks/final.md"
    target.write_text("# Upgrade\n", encoding="utf-8")
    (stack / "runbooks/upgrade.md").symlink_to("./content/../final.md")
    (stack / "docs/upgrade.md").symlink_to("../runbooks/upgrade.md")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add normalized runbook symlink chain")
    commit = git(repository, "rev-parse", "HEAD")
    reader = lambda owner, name: GitHubRepositoryState("main", commit)

    clean = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )
    target.write_text("locally changed\n", encoding="utf-8")
    dirty = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )

    assert clean.errors == ()
    assert clean.snapshot is not None
    assert dirty.snapshot is not None
    clean_stack = clean.snapshot.stacks[0]
    dirty_stack = dirty.snapshot.stacks[0]
    assert clean_stack.complete is True
    assert clean_stack.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/docs/upgrade.md",
        "stacks/media/example/runbooks/final.md",
        "stacks/media/example/runbooks/upgrade.md",
        "stacks/media/example/stack.yaml",
    )
    assert dirty_stack.complete is False
    assert dirty_stack.changed_inputs == (
        "stacks/media/example/runbooks/final.md",
    )
    git(repository, "add", "stacks/media/example/runbooks/final.md")
    git(repository, "commit", "-m", "change normalized runbook target")
    changed_commit = git(repository, "rev-parse", "HEAD")
    committed = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState(
            "main", changed_commit
        ),
    )

    assert committed.snapshot is not None
    committed_stack = committed.snapshot.stacks[0]
    assert committed_stack.complete is True
    assert committed_stack.fingerprint != clean_stack.fingerprint


def test_checked_in_runbook_symlink_target_cannot_escape_selected_stack(
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
    (stack / "docs").mkdir()
    external_target = repository / "stacks/media/shared.md"
    external_target.write_text("# Not this stack's runbook\n", encoding="utf-8")
    (stack / "docs/upgrade.md").symlink_to("../../shared.md")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add escaping runbook symlink")
    commit = git(repository, "rev-parse", "HEAD")
    reader = lambda owner, name: GitHubRepositoryState("main", commit)

    clean = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )
    external_target.write_text(
        "locally changed outside selected stack\n", encoding="utf-8"
    )
    dirty_external = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )

    assert clean.snapshot is not None
    assert dirty_external.snapshot is not None
    clean_stack = clean.snapshot.stacks[0]
    dirty_stack = dirty_external.snapshot.stacks[0]
    assert clean_stack.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/docs/upgrade.md",
        "stacks/media/example/stack.yaml",
    )
    assert dirty_stack == clean_stack


@pytest.mark.parametrize(
    ("changed_input", "staged"),
    [
        ("stacks/media/example/guide.md", False),
        ("stacks/media/example/guide.md", True),
        ("stacks/media/example/runbooks/upgrade.md", False),
        ("stacks/media/example/runbooks/upgrade.md", True),
    ],
)
def test_checked_in_leaf_symlink_and_target_dirtiness_is_stack_local(
    tmp_path: Path, changed_input: str, staged: bool
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
    runbook: guide.md
""",
        encoding="utf-8",
    )
    (stack / "runbooks").mkdir()
    target = stack / "runbooks/upgrade.md"
    target.write_text("# Upgrade\n", encoding="utf-8")
    link = stack / "guide.md"
    link.symlink_to("runbooks/upgrade.md")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add symlinked runbook")
    commit = git(repository, "rev-parse", "HEAD")
    reader = lambda owner, name: GitHubRepositoryState("main", commit)
    clean = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )

    if changed_input.endswith("guide.md"):
        link.unlink()
        link.symlink_to("runbooks/local.md")
    else:
        target.write_text("locally changed\n", encoding="utf-8")
    if staged:
        git(repository, "add", changed_input)
    dirty = build_repository_snapshot(
        repository, ["stacks/media/example"], github_reader=reader
    )

    assert clean.snapshot is not None
    assert dirty.snapshot is not None
    clean_stack = clean.snapshot.stacks[0]
    dirty_stack = dirty.snapshot.stacks[0]
    assert clean_stack.complete is True
    assert clean_stack.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/guide.md",
        "stacks/media/example/runbooks/upgrade.md",
        "stacks/media/example/stack.yaml",
    )
    assert dirty_stack.relevant_inputs == clean_stack.relevant_inputs
    assert dirty_stack.changed_inputs == (changed_input,)
    assert dirty_stack.complete is False
    assert dirty_stack.fingerprint == clean_stack.fingerprint


def test_unchecked_in_symlink_substitution_never_follows_external_runbook(
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
    git(repository, "commit", "-m", "declare absent runbook")
    commit = git(repository, "rev-parse", "HEAD")
    external = tmp_path / "external"
    external.mkdir()
    (external / "upgrade.md").write_text("external secret\n", encoding="utf-8")
    (stack / "docs").symlink_to(external, target_is_directory=True)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/docs/upgrade.md",
        "stacks/media/example/stack.yaml",
    )
    assert snapshot.changed_inputs == (
        "stacks/media/example/docs/upgrade.md",
    )
    assert snapshot.complete is False


def test_symlinked_runbook_parent_outside_repository_makes_stack_incomplete(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    stack = repository / "stacks/media/example"
    policy_path = "stacks/media/example/stack.yaml"
    runbook_path = "stacks/media/example/docs/upgrade.md"
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
    checked_in_runbook = stack / "docs/upgrade.md"
    checked_in_runbook.write_text("# Upgrade\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add assisted runbook")
    commit = git(repository, "rev-parse", "HEAD")

    external_docs = tmp_path / "external-docs"
    external_docs.mkdir()
    (external_docs / "upgrade.md").write_bytes(checked_in_runbook.read_bytes())
    checked_in_runbook.unlink()
    (stack / "docs").rmdir()
    (stack / "docs").symlink_to(external_docs, target_is_directory=True)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        runbook_path,
        policy_path,
    )
    assert snapshot.changed_inputs == (runbook_path,)
    assert snapshot.complete is False


def test_symlinked_stack_directory_outside_repository_makes_stack_incomplete(
    tmp_path: Path,
) -> None:
    repository, commit = create_repository(tmp_path)
    stack_identity = "stacks/media/example"
    stack = repository / stack_identity
    external_stack = tmp_path / "external-stack"
    stack.rename(external_stack)
    stack.symlink_to(external_stack, target_is_directory=True)

    result = build_repository_snapshot(
        repository,
        [stack_identity],
        github_reader=lambda owner, name: GitHubRepositoryState("main", commit),
    )

    assert result.errors == ()
    assert result.snapshot is not None
    snapshot = result.snapshot.stacks[0]
    assert snapshot.relevant_inputs == (
        "stacks/media/example/compose.yaml",
        "stacks/media/example/stack.yaml",
    )
    assert snapshot.changed_inputs == snapshot.relevant_inputs
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


def test_deep_checked_in_policy_has_snapshot_without_runbook(tmp_path: Path) -> None:
    repository, _ = create_repository(tmp_path)
    policy_path = "stacks/media/example/stack.yaml"
    (repository / policy_path).write_text("- " * 2000 + "value\n", encoding="utf-8")
    git(repository, "add", policy_path)
    git(repository, "commit", "-m", "add deeply nested policy")
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


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/example/bad?query.git",
        "https://github.com/example/bad#fragment.git",
        "https://github.com/example/bad%2Frepository.git",
        "https://github.com/example/bad\\repository.git",
        "https://github.com/example/bad\tname.git",
        "https://github.com/./homelab.git",
        "https://github.com/example/..git",
        "https://github.com/example/trailing..git",
    ],
)
def test_endpoint_unsafe_github_origin_fails_before_reader(
    tmp_path: Path, origin: str
) -> None:
    repository, _ = create_repository(tmp_path)
    git(repository, "remote", "set-url", "origin", origin)

    def unexpected_reader(owner: str, name: str) -> GitHubRepositoryState:
        raise AssertionError(f"reader called for {owner}/{name}")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=unexpected_reader,
    )

    assert result.snapshot is None
    assert [(error.code, error.path) for error in result.errors] == [
        ("invalid-origin", "repository.origin")
    ]


@pytest.mark.parametrize("line_ending", ["\n", "\r"])
def test_origin_line_ending_is_preserved_and_rejected_before_reader(
    tmp_path: Path, line_ending: str
) -> None:
    repository, _ = create_repository(tmp_path)
    git(
        repository,
        "config",
        "remote.origin.url",
        f"https://github.com/example/homelab.git{line_ending}",
    )

    def unexpected_reader(owner: str, name: str) -> GitHubRepositoryState:
        raise AssertionError(f"reader called for {owner}/{name}")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=unexpected_reader,
    )

    assert result.snapshot is None
    assert [(error.code, error.path) for error in result.errors] == [
        ("invalid-origin", "repository.origin")
    ]


def test_multiple_origin_urls_are_ambiguous_and_fail_before_reader(
    tmp_path: Path,
) -> None:
    repository, _ = create_repository(tmp_path)
    git(
        repository,
        "config",
        "--add",
        "remote.origin.url",
        "git@github.com:other/homelab.git",
    )

    def unexpected_reader(owner: str, name: str) -> GitHubRepositoryState:
        raise AssertionError(f"reader called for {owner}/{name}")

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=unexpected_reader,
    )

    assert result.snapshot is None
    assert [(error.code, error.path) for error in result.errors] == [
        ("invalid-origin", "repository.origin")
    ]


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/example-org/homelab_iac.config-2.git",
        "https://github.com/example-org/homelab_iac.config-2",
        "git@github.com:example-org/homelab_iac.config-2.git",
        "ssh://git@github.com/example-org/homelab_iac.config-2.git",
    ],
)
def test_supported_github_origin_forms_resolve_safe_identity(
    tmp_path: Path, origin: str
) -> None:
    repository, commit = create_repository(tmp_path)
    git(repository, "remote", "set-url", "origin", origin)
    identities: list[tuple[str, str]] = []

    def reader(owner: str, name: str) -> GitHubRepositoryState:
        identities.append((owner, name))
        return GitHubRepositoryState("main", commit)

    result = build_repository_snapshot(
        repository,
        ["stacks/media/example"],
        github_reader=reader,
    )

    assert result.errors == ()
    assert result.snapshot is not None
    assert result.snapshot.repository == "example-org/homelab_iac.config-2"
    assert identities == [("example-org", "homelab_iac.config-2")]


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


def test_non_utf8_github_process_failure_is_structured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _ = create_repository(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import os

os.write(2, b"\\xffunsafe stderr")
raise SystemExit(1)
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

    result = build_repository_snapshot(repository, ["stacks/media/example"])

    assert result.snapshot is None
    assert [error.as_dict() for error in result.errors] == [
        {
            "code": "github-read-failed",
            "message": "GitHub repository could not be read",
            "path": "repository.github",
        }
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
