from __future__ import annotations

import subprocess
from pathlib import Path

# Commits are authored by the platform, never by the user's global identity.
IDENTITY = ("-c", "user.name=MRLC Runtime", "-c", "user.email=runtime@mrlc.invalid",
            "-c", "commit.gpgsign=false")
IGNORED = "__pycache__/\n.pytest_cache/\n*.pyc\n*.sqlite3\n"


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, capture_output=True,
                               text=True, timeout=60)
    if completed.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {completed.stderr.strip()[:500]}")
    return completed.stdout.strip()


def ensure_repository(path: Path, branch: str = "main") -> str:
    """Create a project repository with one baseline commit; return the branch head."""
    if not (path / ".git").exists():
        path.mkdir(parents=True, exist_ok=True)
        git(path, "init", "-q", "-b", branch)
        (path / ".gitignore").write_text(IGNORED)
        git(path, "add", ".gitignore")
        git(path, "commit", "-q", "-m", "chore: project baseline")
    return git(path, "rev-parse", branch)


def add_worktree(repository: Path, worktree: Path, branch: str, base: str) -> None:
    worktree.parent.mkdir(parents=True, exist_ok=True)
    git(repository, "worktree", "add", "-q", "-b", branch, str(worktree), base)


def commit_all(worktree: Path, message: str) -> str | None:
    git(worktree, "add", "-A")
    if not git(worktree, "status", "--porcelain"):
        return None
    git(worktree, "commit", "-q", "-m", message)
    return git(worktree, "rev-parse", "HEAD")


def changed_files(worktree: Path, base: str) -> list[str]:
    output = git(worktree, "diff", "--name-only", base, "HEAD")
    return [line for line in output.splitlines() if line]


def merge_reviewed(repository: Path, branch: str, target: str, message: str) -> str:
    """Integrate a reviewed child branch with an explicit merge commit on the target branch."""
    if git(repository, "rev-parse", "--abbrev-ref", "HEAD") != target:
        raise RuntimeError("project repository is not on its integration branch")
    if git(repository, "status", "--porcelain"):
        raise RuntimeError("project repository has uncommitted changes")
    try:
        git(repository, "merge", "--no-ff", "-m", message, branch)
    except RuntimeError:
        subprocess.run(["git", "merge", "--abort"], cwd=repository, capture_output=True, timeout=60)
        raise
    return git(repository, "rev-parse", "HEAD")
