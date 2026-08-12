"""Canonical read-only repository inputs for future image update plans."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import yaml


GitHubReader = Callable[[str, str], "GitHubRepositoryState"]


class GitHubReadError(Exception):
    """A safe diagnostic from the controlled GitHub read boundary."""


class _RepositoryInputReadError(Exception):
    def __init__(self, path: str) -> None:
        self.path = path


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
        if not _is_valid_git_branch_name(default_branch):
            return GitHubRepositoryState(default_branch, "")
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


def _is_filesystem_encodable(path: str) -> bool:
    if "\0" in path or any(0xD800 <= ord(character) <= 0xDFFF for character in path):
        return False
    try:
        encoded = os.fsencode(path)
    except UnicodeEncodeError:
        return False
    return os.fsdecode(encoded) == path


def _is_valid_git_branch_name(branch: str) -> bool:
    if not _is_filesystem_encodable(branch) or any(
        ord(character) < 32 or ord(character) == 127 for character in branch
    ):
        return False
    try:
        completed = subprocess.run(
            ["git", "check-ref-format", "--branch", branch],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return completed.stdout.strip() == branch


def _tree_entry(
    repository: Path, treeish: str, path: str
) -> tuple[str, str, str] | None:
    raw = _git(repository, "ls-tree", "-z", treeish, "--", path, text=False)
    assert isinstance(raw, bytes)
    entries = [entry for entry in raw.split(b"\0") if entry]
    if len(entries) != 1:
        return None
    metadata, separator, entry_path = entries[0].partition(b"\t")
    fields = metadata.split()
    if not separator or entry_path != path.encode() or len(fields) != 3:
        return None
    try:
        return (
            fields[0].decode("ascii"),
            fields[1].decode("ascii"),
            fields[2].decode("ascii"),
        )
    except UnicodeDecodeError:
        return None


def _index_has_path(repository: Path, path: str) -> bool:
    raw = _git(repository, "ls-files", "--cached", "-z", "--", path, text=False)
    assert isinstance(raw, bytes)
    encoded_path = path.encode()
    return any(
        indexed_path == encoded_path or indexed_path.startswith(encoded_path + b"/")
        for indexed_path in raw.split(b"\0")
        if indexed_path
    )


def _fingerprint(repository: Path, paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        try:
            entry = _tree_entry(repository, "HEAD", path)
            if entry is None:
                raise subprocess.CalledProcessError(1, ["git", "ls-tree"])
            mode, object_type, object_id = entry
            if object_type == "blob":
                identity = _git(repository, "show", f"HEAD:{path}", text=False)
                assert isinstance(identity, bytes)
            else:
                identity = object_id.encode()
            for component in (
                path.encode(),
                mode.encode(),
                object_type.encode(),
                identity,
            ):
                digest.update(len(component).to_bytes(8, "big"))
                digest.update(component)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise _RepositoryInputReadError(path) from exc
    return digest.hexdigest()


def _blob_differs_from_worktree(
    repository: Path, path: str, head_mode: str
) -> bool:
    checked_in = _git(repository, "show", f"HEAD:{path}", text=False)
    assert isinstance(checked_in, bytes)
    filesystem_path = repository / path
    if not os.path.lexists(filesystem_path):
        return True
    filesystem_mode = filesystem_path.lstat().st_mode
    if stat.S_ISREG(filesystem_mode):
        worktree_mode = "100755" if filesystem_mode & 0o111 else "100644"
        content = filesystem_path.read_bytes()
    elif stat.S_ISLNK(filesystem_mode):
        worktree_mode = "120000"
        content = os.fsencode(os.readlink(filesystem_path))
    else:
        return True
    return worktree_mode != head_mode or content != checked_in


def _tree_differs_from_worktree(repository: Path, path: str) -> bool:
    root = repository / path
    if not root.is_dir() or root.is_symlink():
        return True
    raw = _git(
        repository, "ls-tree", "-r", "-t", "-z", "HEAD", "--", path, text=False
    )
    assert isinstance(raw, bytes)
    expected_paths: set[str] = set()
    descendant_prefix = f"{path}/"
    for raw_entry in (entry for entry in raw.split(b"\0") if entry):
        metadata, separator, raw_path = raw_entry.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            return True
        entry_path = os.fsdecode(raw_path)
        if not entry_path.startswith(descendant_prefix):
            continue
        mode, object_type, _object_id = (field.decode("ascii") for field in fields)
        expected_paths.add(entry_path)
        filesystem_path = repository / entry_path
        if object_type == "tree":
            if not filesystem_path.is_dir() or filesystem_path.is_symlink():
                return True
        elif object_type == "blob":
            if _blob_differs_from_worktree(repository, entry_path, mode):
                return True
        elif not os.path.lexists(filesystem_path):
            return True
    actual_paths = {
        item.relative_to(repository).as_posix() for item in root.rglob("*")
    }
    return actual_paths != expected_paths


def _gitlink_differs_from_worktree(
    repository: Path, path: str, head_object_id: str
) -> bool:
    filesystem_path = repository / path
    if not filesystem_path.is_dir() or filesystem_path.is_symlink():
        return True
    top_level = Path(
        str(_git(filesystem_path, "rev-parse", "--show-toplevel")).strip()
    ).resolve()
    if top_level != filesystem_path.resolve():
        return True
    worktree_commit = str(
        _git(filesystem_path, "rev-parse", "--verify", "HEAD^{commit}")
    ).strip()
    if worktree_commit != head_object_id:
        return True
    nested_status = str(
        _git(
            filesystem_path,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
    )
    return bool(nested_status)


def _working_tree_differs_from_head(repository: Path, path: str) -> bool:
    entry = _tree_entry(repository, "HEAD", path)
    if entry is None:
        return True
    head_mode, head_type, head_object_id = entry
    if head_type == "blob":
        return _blob_differs_from_worktree(repository, path, head_mode)
    if head_type == "tree":
        return _tree_differs_from_worktree(repository, path)
    if head_mode == "160000" and head_type == "commit":
        return _gitlink_differs_from_worktree(repository, path, head_object_id)
    return not os.path.lexists(repository / path)


def _index_differs_from_head(repository: Path, path: str) -> bool:
    try:
        _git(repository, "diff-index", "--cached", "--quiet", "HEAD", "--", path)
        return False
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 1:
            return True
        raise


def _checked_in_runbook(repository: Path, stack_identity: str) -> str | None:
    manifest_path = f"{stack_identity}/stack.yaml"
    entry = _tree_entry(repository, "HEAD", manifest_path)
    if entry is None or entry[1] != "blob":
        return None
    try:
        content = _git(repository, "show", f"HEAD:{manifest_path}", text=False)
        assert isinstance(content, bytes)
        metadata = yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
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
    raw_runbook_path = runbook.partition("#")[0]
    if not _is_filesystem_encodable(raw_runbook_path) or any(
        ord(character) < 32 or ord(character) == 127
        for character in raw_runbook_path
    ):
        return None
    try:
        runbook_path = PurePosixPath(raw_runbook_path)
    except ValueError:
        return None
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
    if not _is_valid_git_branch_name(remote.default_branch):
        error = SnapshotError(
            "invalid-default-branch",
            "repository.default_branch",
            "GitHub returned an invalid default branch",
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

    stack_identities = tuple(selected_stacks)
    for stack_identity in stack_identities:
        if (
            not isinstance(stack_identity, str)
            or not _is_filesystem_encodable(stack_identity)
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in stack_identity
            )
        ):
            error = SnapshotError(
                "invalid-stack-identity",
                "stack.identity",
                "selected stack identity must be stacks/<host>/<stack>",
            )
            return RepositorySnapshotBuild((error,), None)
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

    selected_stack_identities = tuple(sorted(set(stack_identities)))
    stacks: list[StackInputSnapshot] = []
    supported = (
        "compose.override.yaml",
        "compose.override.yml",
        "compose.yaml",
        "compose.yml",
        "stack.yaml",
    )
    for stack_identity in selected_stack_identities:
        current_path = f"{stack_identity}/stack.yaml"
        try:
            candidates = tuple(
                f"{stack_identity}/{filename}" for filename in supported
            )
            runbook = _checked_in_runbook(resolved_root, stack_identity)
            if runbook is not None:
                candidates += (runbook,)
            candidates = tuple(sorted(set(candidates)))
            checked_in: dict[str, bool] = {}
            indexed: dict[str, bool] = {}
            for path in candidates:
                current_path = path
                checked_in[path] = (
                    _tree_entry(resolved_root, "HEAD", path) is not None
                )
                indexed[path] = _index_has_path(resolved_root, path)
            paths = tuple(
                sorted(
                    path
                    for path in candidates
                    if checked_in[path]
                    or indexed[path]
                    or os.path.lexists(resolved_root / path)
                )
            )
            if not paths:
                error = SnapshotError(
                    "missing-stack-inputs",
                    stack_identity,
                    "selected stack has no checked-in or locally added relevant inputs",
                )
                return RepositorySnapshotBuild((error,), None)
            changed_paths: list[str] = []
            for path in paths:
                current_path = path
                if _index_differs_from_head(
                    resolved_root, path
                ) or _working_tree_differs_from_head(resolved_root, path):
                    changed_paths.append(path)
            changed = tuple(changed_paths)
            checked_in_paths = tuple(path for path in paths if checked_in[path])
            fingerprint = _fingerprint(resolved_root, checked_in_paths)
            stacks.append(
                StackInputSnapshot(
                    stack_identity,
                    not changed,
                    fingerprint,
                    paths,
                    changed,
                )
            )
        except _RepositoryInputReadError as exc:
            current_path = exc.path
        except (OSError, subprocess.CalledProcessError):
            pass
        else:
            continue
        error = SnapshotError(
            "repository-input-read-failed",
            current_path,
            "repository input could not be read",
        )
        return RepositorySnapshotBuild((error,), None)
    snapshot = RepositorySnapshot(
        f"{owner}/{name}", remote.default_branch, remote.commit, tuple(stacks)
    )
    return RepositorySnapshotBuild((), snapshot)
