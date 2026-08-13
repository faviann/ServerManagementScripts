"""Policy validation and read-only inputs for repo-managed stack updates."""

from .validation import StackPolicyValidation, validate_stack
from .snapshot import (
    GitHubReadError,
    GitHubRepositoryState,
    RepositorySnapshot,
    RepositorySnapshotBuild,
    SnapshotError,
    StackInputSnapshot,
    build_repository_snapshot,
    read_github_repository,
)
from .selection import (
    GitHubProposal,
    GitHubProposalReadError,
    ProposalMarker,
    SelectedStack,
    SelectionError,
    StackSelection,
    StackSelectionBuild,
    build_stack_selection,
    read_github_proposals,
)

__all__ = [
    "GitHubReadError",
    "GitHubProposal",
    "GitHubProposalReadError",
    "GitHubRepositoryState",
    "RepositorySnapshot",
    "RepositorySnapshotBuild",
    "SnapshotError",
    "ProposalMarker",
    "SelectedStack",
    "SelectionError",
    "StackSelection",
    "StackSelectionBuild",
    "StackInputSnapshot",
    "StackPolicyValidation",
    "build_repository_snapshot",
    "build_stack_selection",
    "read_github_proposals",
    "read_github_repository",
    "validate_stack",
]
