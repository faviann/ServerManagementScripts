import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from stack_update_policy import (  # noqa: E402
    GitHubProposal,
    GitHubProposalReadError,
    build_stack_selection,
)


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()


def create_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Selection Test")
    git(repository, "config", "user.email", "selection@example.invalid")
    git(repository, "remote", "add", "origin", "https://github.com/example/homelab.git")
    return repository


def add_stack(
    repository: Path, host: str, name: str, *, policy: bool = True
) -> None:
    stack = repository / "stacks" / host / name
    stack.mkdir(parents=True)
    (stack / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    if policy:
        (stack / "stack.yaml").write_text(
            "updates:\n  mode: images\n  track: stable\n", encoding="utf-8"
        )


def commit(repository: Path) -> None:
    git(repository, "add", ".")
    git(repository, "commit", "-m", "fixture")


def proposal(
    number: int,
    state: str,
    identity: str,
    fingerprint: str = "a" * 64,
) -> GitHubProposal:
    marker = (
        '<!-- image-update-proposal:v1 {"fingerprint":'
        f'"{fingerprint}","stack":"{identity}"}} -->'
    )
    return GitHubProposal(number, state, marker)


def test_default_selection_contains_current_repo_managed_stacks(tmp_path: Path) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "example")
    commit(repository)

    result = build_stack_selection(repository, proposal_reader=lambda owner, name: ())

    assert result.as_dict() == {
        "errors": [],
        "schema_version": 1,
        "selection": {
            "repository": "example/homelab",
            "stacks": [
                {
                    "closed_proposals": [],
                    "complete": True,
                    "host": "media",
                    "identity": "stacks/media/example",
                    "legacy": False,
                    "name": "example",
                    "open_proposals": [],
                    "policy_status": "configured",
                    "source": "current",
                }
            ],
        },
        "valid": True,
    }


def test_default_selection_unions_open_proposal_only_identity_and_loads_closed_for_current(
    tmp_path: Path,
) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "current")
    commit(repository)
    proposals = (
        proposal(4, "OPEN", "stacks/retired/orphan"),
        proposal(3, "CLOSED", "stacks/media/current", "b" * 64),
        proposal(2, "CLOSED", "stacks/retired/closed-only", "c" * 64),
    )

    result = build_stack_selection(
        repository, proposal_reader=lambda owner, name: proposals
    )

    assert result.valid
    assert result.selection is not None
    assert [stack.identity for stack in result.selection.stacks] == [
        "stacks/media/current",
        "stacks/retired/orphan",
    ]
    current, orphan = result.selection.stacks
    assert [item.number for item in current.closed_proposals] == [3]
    assert orphan.source == "proposal-only"
    assert orphan.policy_status == "not-applicable"


def test_exact_repeated_filters_or_within_dimensions_and_and_across_them(
    tmp_path: Path,
) -> None:
    repository = create_repository(tmp_path)
    for host, name in (("alpha", "one"), ("alpha", "two"), ("beta", "one")):
        add_stack(repository, host, name)
    commit(repository)

    result = build_stack_selection(
        repository,
        host_filters=("alpha", "beta"),
        stack_filters=("two",),
        proposal_reader=lambda owner, name: (),
    )

    assert result.valid
    assert result.selection is not None
    assert [stack.identity for stack in result.selection.stacks] == ["stacks/alpha/two"]


def test_filters_match_open_proposals_and_reject_no_match_or_patterns(
    tmp_path: Path,
) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "example")
    commit(repository)
    proposals = (proposal(1, "OPEN", "stacks/retired/orphan"),)
    reader = lambda owner, name: proposals

    proposal_only = build_stack_selection(
        repository, host_filters=("retired",), proposal_reader=reader
    )
    unmatched = build_stack_selection(
        repository, stack_filters=("typo",), proposal_reader=reader
    )
    partially_unmatched = build_stack_selection(
        repository,
        stack_filters=("example", "typo"),
        proposal_reader=reader,
    )
    pattern = build_stack_selection(
        repository, host_filters=("med*",), proposal_reader=reader
    )

    assert proposal_only.valid
    assert proposal_only.selection is not None
    assert [stack.identity for stack in proposal_only.selection.stacks] == [
        "stacks/retired/orphan"
    ]
    assert [error.code for error in unmatched.errors] == ["selection-no-match"]
    assert [error.code for error in partially_unmatched.errors] == [
        "selection-no-match"
    ]
    assert [error.code for error in pattern.errors] == ["invalid-filter"]


def test_legacy_policy_is_reported_without_invalidating_broad_selection(
    tmp_path: Path,
) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "legacy", policy=False)
    commit(repository)

    result = build_stack_selection(repository, proposal_reader=lambda owner, name: ())

    assert result.valid
    assert result.selection is not None
    stack = result.selection.stacks[0]
    assert stack.legacy is True
    assert stack.complete is True
    assert stack.policy_status == "policy-not-configured"


def test_missing_updates_is_legacy_but_malformed_metadata_is_left_for_validation(
    tmp_path: Path,
) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "missing-updates")
    add_stack(repository, "media", "malformed")
    (repository / "stacks/media/missing-updates/stack.yaml").write_text(
        "schema_version: 1\n", encoding="utf-8"
    )
    (repository / "stacks/media/malformed/stack.yaml").write_text(
        "updates: [broken\n", encoding="utf-8"
    )
    commit(repository)

    result = build_stack_selection(repository, proposal_reader=lambda owner, name: ())

    assert result.valid
    assert result.selection is not None
    assert [(stack.name, stack.policy_status) for stack in result.selection.stacks] == [
        ("malformed", "configured"),
        ("missing-updates", "policy-not-configured"),
    ]


def test_duplicate_open_identity_marks_only_that_stack_incomplete(tmp_path: Path) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "example")
    commit(repository)
    proposals = (
        proposal(8, "OPEN", "stacks/media/example"),
        proposal(7, "OPEN", "stacks/media/example", "b" * 64),
    )

    result = build_stack_selection(
        repository, proposal_reader=lambda owner, name: proposals
    )

    assert result.valid
    assert result.selection is not None
    stack = result.selection.stacks[0]
    assert stack.complete is False
    assert [item.number for item in stack.open_proposals] == [7, 8]


def test_supported_marker_accepts_json_key_order_and_whitespace(tmp_path: Path) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "example")
    commit(repository)
    body = """\
<!-- image-update-proposal:v1 {
  "stack": "stacks/media/example",
  "fingerprint": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
} -->
"""

    result = build_stack_selection(
        repository,
        proposal_reader=lambda owner, name: (GitHubProposal(10, "OPEN", body),),
    )

    assert result.valid
    assert result.selection is not None
    assert result.selection.stacks[0].open_proposals[0].fingerprint == "d" * 64


def test_supported_marker_rejects_duplicate_stack_member(tmp_path: Path) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "example")
    commit(repository)
    body = (
        '<!-- image-update-proposal:v1 {"fingerprint":"'
        + "a" * 64
        + '","stack":"stacks/media/example",'
        '"stack":"stacks/retired/orphan"} -->'
    )

    result = build_stack_selection(
        repository,
        proposal_reader=lambda owner, name: (GitHubProposal(11, "OPEN", body),),
    )

    assert [error.code for error in result.errors] == [
        "untrustworthy-proposal-marker"
    ]
    assert result.selection is None


def test_supported_marker_rejects_duplicate_fingerprint_member(tmp_path: Path) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "example")
    commit(repository)
    body = (
        '<!-- image-update-proposal:v1 {"fingerprint":"'
        + "a" * 64
        + '","stack":"stacks/media/example","fingerprint":"'
        + "b" * 64
        + '"} -->'
    )

    result = build_stack_selection(
        repository,
        proposal_reader=lambda owner, name: (GitHubProposal(12, "OPEN", body),),
    )

    assert [error.code for error in result.errors] == [
        "untrustworthy-proposal-marker"
    ]
    assert result.selection is None


@pytest.mark.parametrize(
    "body",
    [
        '<!-- image-update-proposal:v2 {"fingerprint":"' + "a" * 64 + '","stack":"stacks/media/example"} -->',
        '<!-- image-update-proposal:v1 {not-json} -->',
        '<!-- image-update-proposal:v1 {"fingerprint":"short","stack":"stacks/media/example"} -->',
        '<!-- image-update-proposal:v1 {"fingerprint":"' + "a" * 64 + '","stack":"stacks/media/example"} -->\n'
        '<!-- image-update-proposal:v1 {"fingerprint":"' + "b" * 64 + '","stack":"stacks/media/example"} -->',
    ],
)
def test_untrustworthy_marker_fails_repository_discovery_closed(
    tmp_path: Path, body: str
) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "example")
    commit(repository)

    result = build_stack_selection(
        repository,
        proposal_reader=lambda owner, name: (GitHubProposal(9, "OPEN", body),),
    )

    assert [error.code for error in result.errors] == ["untrustworthy-proposal-marker"]
    assert result.selection is None


def test_github_proposal_read_failure_is_safe_and_global(tmp_path: Path) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "example")
    commit(repository)

    def failing_reader(owner: str, name: str):
        raise GitHubProposalReadError("GitHub proposals could not be read")

    result = build_stack_selection(repository, proposal_reader=failing_reader)

    assert [error.as_dict() for error in result.errors] == [
        {
            "code": "github-proposal-read-failed",
            "message": "GitHub proposals could not be read",
            "path": "repository.github.proposals",
        }
    ]


def test_default_github_boundary_reads_issues_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = create_repository(tmp_path)
    add_stack(repository, "media", "example")
    commit(repository)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    invocation = tmp_path / "invocation.json"
    gh = fake_bin / "gh"
    gh.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$INVOCATION\"\nprintf '%s\\n' '[[{\"number\":12,\"state\":\"open\",\"body\":\"<!-- image-update-proposal:v1 {\\\"fingerprint\\\":\\\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\\\",\\\"stack\\\":\\\"stacks/media/example\\\"} -->\"}]]'\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("INVOCATION", str(invocation))

    result = build_stack_selection(repository)

    assert result.valid
    assert invocation.read_text(encoding="utf-8").strip() == (
        "api --paginate --slurp repos/example/homelab/issues?state=all&per_page=100"
    )
    assert result.selection is not None
    assert result.selection.stacks[0].open_proposals[0].number == 12
