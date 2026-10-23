"""Session-wide guard: the test suite must not pollute the working tree.

A test that touches a fictional path creates real entries in the caller's
working directory (issue #38: an encode payload of 'Z:/out' is a relative
path on POSIX, so the job engine created a literal 'Z:' directory next to
the checkout). `git status --porcelain` is blind to empty directories —
the exact shape of that defect — so the listing comes from
`git ls-files --others --directory`, which reports untracked directories
including empty ones.

The check compares before/after the session: pre-existing untracked entries
(local agent config, tool caches) are not the suite's doing and never flagged.
In a checkout shared by concurrent agents, an entry created by another process
during the run is flagged too — the message names it, so it is diagnosable.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _untracked_entries() -> set[str] | None:
    """Returns the checkout's untracked entries (empty directories included).

    Returns None when there is nothing to guard against (no git, no checkout,
    e.g. an installed sdist): the check simply does not run then.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--directory", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return set(result.stdout.splitlines())


@pytest.fixture(scope="session", autouse=True)
def _no_working_tree_pollution() -> Iterator[None]:
    """Fails the session when a test leaves a new untracked entry behind."""
    before = _untracked_entries()
    yield
    if before is None:
        return
    after = _untracked_entries()
    if after is None:
        return
    created = sorted(after - before)
    assert not created, (
        f"the test suite left untracked paths in the checkout: {created}"
        " - tests write into tempfile.TemporaryDirectory()/tmp_path, never"
        " into the working tree"
    )
