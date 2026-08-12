"""Canonical read-only repository inputs for future image update plans."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import yaml


GitHubReader = Callable[[str, str], "GitHubRepositoryState"]


class GitHubReadError(Exception):
    """A safe diagnostic from the controlled GitHub read boundary."""


@dataclass(frozen=True)
class GitHubRepositoryState:
    default_branch: str
    commit: str


@dataclass(frozen=True)
class SnapshotError:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass(frozen=True)
class StackInputSnapshot:
    identity: str
    complete: bool
    fingerprint: str
    relevant_inputs: tuple[str, ...]
    changed_inputs: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "changed_inputs": list(self.changed_inputs),
            "complete": self.complete,
            "fingerprint": self.fingerprint,
            "identity": self.identity,
            "relevant_inputs": list(self.relevant_inputs),
        }


@dataclass(frozen=True)
class RepositorySnapshot:
    repository: str
    default_branch: str
    commit: str
    stacks: tuple[StackInputSnapshot, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "commit": self.commit,
            "default_branch": self.default_branch,
            "repository": self.repository,
            "stacks": [stack.as_dict() for stack in self.stacks],
        }


@dataclass(frozen=True)
class RepositorySnapshotBuild:
    errors: tuple[SnapshotError, ...]
    snapshot: RepositorySnapshot | None

    def __post_init__(self) -> None:
        if bool(self.errors) == bool(self.snapshot):
            raise ValueError("snapshot build must contain either errors or a snapshot")

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "errors": [error.as_dict() for error in self.errors],
            "schema_version": 1,
            "snapshot": self.snapshot.as_dict() if self.snapshot else None,
            "valid": self.valid,
        }


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


def read_github_repository(owner: str, name: str) -> GitHubRepositoryState:
    """Resolve a repository's current default branch and commit using GitHub reads."""
    try:
        repository = subprocess.run(
            ["gh", "api", "--method", "GET", f"repos/{owner}/{name}"],
            check=True,
            capture_output=True,
            text=True,
        )
        repository_payload = json.loads(repository.stdout)
        if not isinstance(repository_payload, dict):
            raise GitHubReadError("GitHub repository could not be read")
        default_branch = repository_payload.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            return GitHubRepositoryState("", "")
        branch = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                f"repos/{owner}/{name}/commits/{quote(default_branch, safe='')}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        branch_payload = json.loads(branch.stdout)
        if not isinstance(branch_payload, dict):
            raise GitHubReadError("GitHub repository could not be read")
        commit = branch_payload.get("sha")
        return GitHubRepositoryState(
            default_branch, commit if isinstance(commit, str) else ""
        )
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise GitHubReadError("GitHub repository could not be read") from exc


def _github_identity(origin: str) -> tuple[str, str] | None:
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?/?",
        origin.strip(),
    )
    return (match.group(1), match.group(2)) if match else None


def _fingerprint(repository: Path, paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        content = _git(repository, "show", f"HEAD:{path}", text=False)
        assert isinstance(content, bytes)
        encoded_path = path.encode()
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _working_tree_differs_from_head(repository: Path, path: str) -> bool:
    try:
        checked_in = _git(repository, "show", f"HEAD:{path}", text=False)
        assert isinstance(checked_in, bytes)
        return (repository / path).read_bytes() != checked_in
    except (OSError, subprocess.CalledProcessError):
        return True


def _checked_in_runbook(repository: Path, stack_identity: str) -> str | None:
    manifest_path = f"{stack_identity}/stack.yaml"
    try:
        content = _git(repository, "show", f"HEAD:{manifest_path}")
        metadata = yaml.safe_load(str(content))
    except (subprocess.CalledProcessError, yaml.YAMLError):
        return None
    if not isinstance(metadata, dict):
        return None
    updates = metadata.get("updates")
    procedure = updates.get("procedure") if isinstance(updates, dict) else None
    if not isinstance(procedure, dict) or procedure.get("mode") != "assisted":
        return None
    runbook = procedure.get("runbook")
    if not isinstance(runbook, str):
        return None
    runbook_path = PurePosixPath(runbook.partition("#")[0])
    if runbook_path.is_absolute() or not runbook_path.parts or ".." in runbook_path.parts:
        return None
    return (PurePosixPath(stack_identity) / runbook_path).as_posix()


def build_repository_snapshot(
    repository_root: Path,
    selected_stacks: Sequence[str],
    *,
    github_reader: GitHubReader = read_github_repository,
) -> RepositorySnapshotBuild:
    try:
        resolved_root = repository_root.resolve(strict=True)
        top_level = Path(
            str(_git(resolved_root, "rev-parse", "--show-toplevel")).strip()
        ).resolve()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError):
        error = SnapshotError(
            "invalid-repository",
            "repository",
            "repository root is not a readable Git worktree",
        )
        return RepositorySnapshotBuild((error,), None)
    if top_level != resolved_root:
        error = SnapshotError(
            "invalid-repository",
            "repository",
            "repository root must be the Git worktree root",
        )
        return RepositorySnapshotBuild((error,), None)
    try:
        origin = str(_git(resolved_root, "config", "--get", "remote.origin.url")).strip()
    except subprocess.CalledProcessError:
        error = SnapshotError(
            "missing-origin", "repository.origin", "origin remote is required"
        )
        return RepositorySnapshotBuild((error,), None)
    identity = _github_identity(origin)
    if identity is None:
        error = SnapshotError(
            "invalid-origin",
            "repository.origin",
            "origin is not a GitHub repository",
        )
        return RepositorySnapshotBuild((error,), None)
    owner, name = identity
    try:
        remote = github_reader(owner, name)
    except GitHubReadError as exc:
        error = SnapshotError("github-read-failed", "repository.github", str(exc))
        return RepositorySnapshotBuild((error,), None)
    if not isinstance(remote, GitHubRepositoryState):
        error = SnapshotError(
            "invalid-github-response",
            "repository.github",
            "GitHub reader returned an invalid repository state",
        )
        return RepositorySnapshotBuild((error,), None)
    if not isinstance(remote.default_branch, str) or not remote.default_branch:
        error = SnapshotError(
            "missing-default-branch",
            "repository.default_branch",
            "GitHub did not resolve a default branch",
        )
        return RepositorySnapshotBuild((error,), None)
    if not isinstance(remote.commit, str) or not remote.commit:
        error = SnapshotError(
            "missing-default-branch-commit",
            "repository.commit",
            "GitHub did not resolve the default-branch commit",
        )
        return RepositorySnapshotBuild((error,), None)
    if re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", remote.commit) is None:
        error = SnapshotError(
            "invalid-default-branch-commit",
            "repository.commit",
            "GitHub returned an invalid default-branch commit",
        )
        return RepositorySnapshotBuild((error,), None)
    try:
        commit = str(_git(resolved_root, "rev-parse", "HEAD")).strip()
    except subprocess.CalledProcessError:
        error = SnapshotError(
            "missing-local-commit",
            "repository.head",
            "local HEAD does not resolve to a commit",
        )
        return RepositorySnapshotBuild((error,), None)
    if commit != remote.commit:
        error = SnapshotError(
            "unauthorized-head",
            "repository.head",
            "HEAD is not the remote default-branch commit",
        )
        return RepositorySnapshotBuild((error,), None)

    for stack_identity in sorted(set(selected_stacks)):
        parts = PurePosixPath(stack_identity).parts
        if (
            len(parts) != 3
            or parts[0] != "stacks"
            or any(part in {"", ".", ".."} for part in parts)
            or PurePosixPath(stack_identity).as_posix() != stack_identity
        ):
            error = SnapshotError(
                "invalid-stack-identity",
                "stack.identity",
                "selected stack identity must be stacks/<host>/<stack>",
            )
            return RepositorySnapshotBuild((error,), None)

    stacks: list[StackInputSnapshot] = []
    supported = (
        "compose.override.yaml",
        "compose.override.yml",
        "compose.yaml",
        "compose.yml",
        "stack.yaml",
    )
    for stack_identity in sorted(set(selected_stacks)):
        tracked = set(
            str(
                _git(
                    resolved_root,
                    "ls-tree",
                    "-r",
                    "--name-only",
                    "HEAD",
                    "--",
                    stack_identity,
                )
            ).splitlines()
        )
        candidates = tuple(f"{stack_identity}/{filename}" for filename in supported)
        runbook = _checked_in_runbook(resolved_root, stack_identity)
        if runbook is not None:
            candidates += (runbook,)
        paths = tuple(
            sorted(
                path
                for path in candidates
                if path in tracked or (resolved_root / path).exists()
            )
        )
        if not paths:
            error = SnapshotError(
                "missing-stack-inputs",
                stack_identity,
                "selected stack has no checked-in or locally added relevant inputs",
            )
            return RepositorySnapshotBuild((error,), None)
        changed = tuple(
            path
            for path in paths
            if _working_tree_differs_from_head(resolved_root, path)
        )
        checked_in_paths = tuple(path for path in paths if path in tracked)
        stacks.append(
            StackInputSnapshot(
                stack_identity,
                not changed,
                _fingerprint(resolved_root, checked_in_paths),
                paths,
                changed,
            )
        )
    snapshot = RepositorySnapshot(
        f"{owner}/{name}", remote.default_branch, remote.commit, tuple(stacks)
    )
    return RepositorySnapshotBuild((), snapshot)
