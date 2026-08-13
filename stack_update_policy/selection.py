"""Read-only selection of current and proposal-only stack identities."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


_MARKER_PREFIX = "<!-- image-update-proposal:"
_MARKER = re.compile(
    r"<!-- image-update-proposal:v1\s+(\{.*?\})\s*-->", re.DOTALL
)
_IDENTITY = re.compile(
    r"stacks/([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)"
)
_FILTER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_GITHUB_READ_TIMEOUT_SECONDS = 30


class GitHubProposalReadError(Exception):
    """A safe diagnostic from the controlled proposal-read boundary."""


@dataclass(frozen=True)
class GitHubProposal:
    number: int
    state: str
    body: str


ProposalReader = Callable[[str, str], Sequence[GitHubProposal]]


@dataclass(frozen=True)
class ProposalMarker:
    number: int
    state: str
    identity: str
    fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "number": self.number,
            "state": self.state.lower(),
        }


@dataclass(frozen=True)
class SelectionError:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass(frozen=True)
class SelectedStack:
    identity: str
    host: str
    name: str
    source: str
    complete: bool
    legacy: bool
    policy_status: str
    open_proposals: tuple[ProposalMarker, ...]
    closed_proposals: tuple[ProposalMarker, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "closed_proposals": [item.as_dict() for item in self.closed_proposals],
            "complete": self.complete,
            "host": self.host,
            "identity": self.identity,
            "legacy": self.legacy,
            "name": self.name,
            "open_proposals": [item.as_dict() for item in self.open_proposals],
            "policy_status": self.policy_status,
            "source": self.source,
        }


@dataclass(frozen=True)
class StackSelection:
    repository: str
    stacks: tuple[SelectedStack, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "stacks": [stack.as_dict() for stack in self.stacks],
        }


@dataclass(frozen=True)
class StackSelectionBuild:
    errors: tuple[SelectionError, ...]
    selection: StackSelection | None

    def __post_init__(self) -> None:
        if bool(self.errors) == bool(self.selection):
            raise ValueError("selection build must contain either errors or a selection")

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "errors": [error.as_dict() for error in self.errors],
            "schema_version": 1,
            "selection": self.selection.as_dict() if self.selection else None,
            "valid": self.valid,
        }


def read_github_proposals(owner: str, name: str) -> tuple[GitHubProposal, ...]:
    """Read all repository issues without performing GitHub mutations."""
    try:
        completed = subprocess.run(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{owner}/{name}/issues?state=all&per_page=100",
            ],
            check=True,
            capture_output=True,
            text=False,
            timeout=_GITHUB_READ_TIMEOUT_SECONDS,
        )
        pages = json.loads(completed.stdout.decode("utf-8"))
        if not isinstance(pages, list) or any(
            not isinstance(page, list) for page in pages
        ):
            raise GitHubProposalReadError("GitHub proposals could not be read")
        proposals: list[GitHubProposal] = []
        for issue in (item for page in pages for item in page):
            if not isinstance(issue, dict):
                raise GitHubProposalReadError("GitHub proposals could not be read")
            if "pull_request" in issue:
                continue
            number = issue.get("number")
            state = issue.get("state")
            body = issue.get("body")
            if (
                type(number) is not int
                or state not in {"open", "closed"}
                or body is not None and not isinstance(body, str)
            ):
                raise GitHubProposalReadError("GitHub proposals could not be read")
            proposals.append(GitHubProposal(number, state.upper(), body or ""))
        return tuple(proposals)
    except GitHubProposalReadError:
        raise
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise GitHubProposalReadError("GitHub proposals could not be read") from exc


def _git(repository: Path, *arguments: str, text: bool = True) -> str | bytes:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=text,
        env=environment,
    )
    return completed.stdout


def _repository_identity(repository: Path) -> tuple[str, str] | None:
    try:
        raw = str(_git(repository, "config", "--get", "remote.origin.url")).strip()
    except subprocess.CalledProcessError:
        return None
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?",
        raw,
    )
    if match is None:
        return None
    owner, name = match.groups()
    if (
        len(owner) > 39
        or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", owner)
        is None
        or len(name) > 100
        or not any(character != "." for character in name)
        or name.endswith(".")
    ):
        return None
    return owner, name


def _current_stacks(repository: Path) -> tuple[set[str], dict[str, str]]:
    raw = _git(
        repository,
        "--literal-pathspecs",
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        "HEAD",
        "--",
        "stacks",
        text=False,
    )
    assert isinstance(raw, bytes)
    paths = [os.fsdecode(path) for path in raw.split(b"\0") if path]
    identities = {
        "/".join(PurePosixPath(path).parts[:3])
        for path in paths
        if len(PurePosixPath(path).parts) == 4
        and PurePosixPath(path).parts[0] == "stacks"
        and PurePosixPath(path).name in {"compose.yaml", "compose.yml"}
        and _IDENTITY.fullmatch("/".join(PurePosixPath(path).parts[:3]))
    }
    policy_status: dict[str, str] = {}
    for identity in identities:
        manifest = f"{identity}/stack.yaml"
        try:
            content = str(_git(repository, "show", f"HEAD:{manifest}"))
        except subprocess.CalledProcessError:
            policy_status[identity] = "policy-not-configured"
            continue
        try:
            metadata: Any = yaml.safe_load(content)
        except yaml.YAMLError:
            policy_status[identity] = "configured"
            continue
        policy_status[identity] = (
            "configured" if isinstance(metadata, dict) and "updates" in metadata
            else "policy-not-configured"
        )
    return identities, policy_status


def _reject_duplicate_json_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate JSON member")
        payload[key] = value
    return payload


def _markers(proposals: Sequence[GitHubProposal]) -> tuple[ProposalMarker, ...]:
    markers: list[ProposalMarker] = []
    for proposal in proposals:
        if (
            not isinstance(proposal, GitHubProposal)
            or type(proposal.number) is not int
            or proposal.number < 1
            or proposal.state not in {"OPEN", "CLOSED"}
            or not isinstance(proposal.body, str)
        ):
            raise GitHubProposalReadError("GitHub proposals could not be read")
        occurrences = proposal.body.count(_MARKER_PREFIX)
        if not occurrences:
            continue
        matches = list(_MARKER.finditer(proposal.body))
        if occurrences != 1 or len(matches) != 1:
            raise ValueError(proposal.number)
        try:
            payload = json.loads(
                matches[0].group(1), object_pairs_hook=_reject_duplicate_json_members
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(proposal.number) from exc
        if not isinstance(payload, dict) or set(payload) != {"fingerprint", "stack"}:
            raise ValueError(proposal.number)
        identity, fingerprint = payload["stack"], payload["fingerprint"]
        if (
            not isinstance(identity, str)
            or _IDENTITY.fullmatch(identity) is None
            or not isinstance(fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        ):
            raise ValueError(proposal.number)
        markers.append(
            ProposalMarker(proposal.number, proposal.state, identity, fingerprint)
        )
    return tuple(sorted(markers, key=lambda item: (item.identity, item.number)))


def build_stack_selection(
    repository_root: Path,
    *,
    host_filters: Sequence[str] = (),
    stack_filters: Sequence[str] = (),
    proposal_reader: ProposalReader = read_github_proposals,
) -> StackSelectionBuild:
    """Build the deterministic read-only scope for image update planning."""
    try:
        repository = repository_root.resolve(strict=True)
        top_level = Path(
            str(_git(repository, "rev-parse", "--show-toplevel")).strip()
        ).resolve()
        identity_parts = _repository_identity(repository)
        if top_level != repository or identity_parts is None:
            raise OSError
        current, policy_statuses = _current_stacks(repository)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError):
        error = SelectionError(
            "invalid-repository",
            "repository",
            "repository root is not a readable GitHub worktree",
        )
        return StackSelectionBuild((error,), None)
    for dimension, values in (("host", host_filters), ("stack", stack_filters)):
        if any(
            not isinstance(value, str) or _FILTER.fullmatch(value) is None
            for value in values
        ):
            error = SelectionError(
                "invalid-filter",
                f"filters.{dimension}",
                f"{dimension} filters must be exact names; patterns are unsupported",
            )
            return StackSelectionBuild((error,), None)
    owner, name = identity_parts
    try:
        proposals = proposal_reader(owner, name)
        markers = _markers(proposals)
    except GitHubProposalReadError as exc:
        error = SelectionError(
            "github-proposal-read-failed", "repository.github.proposals", str(exc)
        )
        return StackSelectionBuild((error,), None)
    except ValueError as exc:
        error = SelectionError(
            "untrustworthy-proposal-marker",
            f"repository.github.issues.{exc.args[0]}",
            "proposal marker is malformed, duplicated, or unsupported",
        )
        return StackSelectionBuild((error,), None)
    open_identities = {marker.identity for marker in markers if marker.state == "OPEN"}
    default_scope = current | open_identities
    available_hosts = {identity.split("/")[1] for identity in default_scope}
    available_stacks = {identity.split("/")[2] for identity in default_scope}
    if any(value not in available_hosts for value in host_filters) or any(
        value not in available_stacks for value in stack_filters
    ):
        error = SelectionError(
            "selection-no-match",
            "filters",
            "a filter matches neither a current stack nor an open proposal",
        )
        return StackSelectionBuild((error,), None)
    selected = {
        identity
        for identity in default_scope
        if (not host_filters or identity.split("/")[1] in host_filters)
        and (not stack_filters or identity.split("/")[2] in stack_filters)
    }
    if (host_filters or stack_filters) and not selected:
        error = SelectionError(
            "selection-no-match",
            "filters",
            "filters match neither a current stack nor an open proposal",
        )
        return StackSelectionBuild((error,), None)
    stacks: list[SelectedStack] = []
    for identity in sorted(selected):
        host, stack_name = identity.split("/")[1:]
        opened = tuple(
            marker
            for marker in markers
            if marker.identity == identity and marker.state == "OPEN"
        )
        closed = (
            tuple(
                marker
                for marker in markers
                if marker.identity == identity and marker.state == "CLOSED"
            )
            if identity in current
            else ()
        )
        is_current = identity in current
        status = policy_statuses[identity] if is_current else "not-applicable"
        stacks.append(
            SelectedStack(
                identity,
                host,
                stack_name,
                "current" if is_current else "proposal-only",
                len(opened) <= 1,
                status == "policy-not-configured",
                status,
                opened,
                closed,
            )
        )
    return StackSelectionBuild((), StackSelection(f"{owner}/{name}", tuple(stacks)))
