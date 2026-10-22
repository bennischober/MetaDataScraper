"""Encode mutual exclusion: one encoder at a time per output directory.

The job queue serializes jobs (one worker thread), but encoders can still
collide from three independent entry points:

- the job worker (UI-initiated encodes)
- the watch loop (in-box automation, runs in its own thread)
- any CLI process (`shrinkray encode ...`) running next to the app

Two encoders writing the same output filename (the watch loop and a job
both encoding an inbox title that also lives in the library produce the
same `output_dir / movie.name`) clobber each other's output — a corrupt
archive that passes the duration check only by luck. A per-output-dir
lock file (pid-stamped, stale-detecting) makes the collision impossible
instead of unlikely.
"""

import contextlib
import os
import subprocess
import time
from pathlib import Path

from shrinkray.logger import log_warning

_LOCK_STALE_SECONDS = 21600.0  # 6 h: long encodes are normal, crashes are not


class EncodeBusyError(RuntimeError):
    """Raised when another encoder already owns the output directory.

    The message names the holder so the user (or the job outcome) can say
    who is encoding and since when.
    """


def _lock_path(output_dir: Path) -> Path:
    """Returns the lock file path for an output directory.

    Args:
        output_dir (Path): The output directory being guarded.

    Returns:
        Path: The lock file inside the output directory.
    """
    return output_dir / ".shrinkray_encode.lock"


def _pid_alive(pid: int) -> bool:
    """Checks whether a pid belongs to a live process.

    Args:
        pid (int): The process id to check.

    Returns:
        bool: True when the process exists (POSIX: os.kill(pid, 0);
            Windows: OpenProcess semantics via a tiny probe).
    """
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            # a Windows process exists iff tasklist finds its pid; decode
            # lossily (the console codepage is not utf-8)
            listing = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                timeout=10,
            )
            return str(pid) in listing.stdout.decode(errors="replace")
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def acquire_encode_slot(output_dir: Path, holder: str = "encode") -> Path:
    """Acquires the encode slot for an output directory (blocking-free).

    Creates the output directory if needed, then claims the lock file
    atomically (O_CREAT|O_EXCL). A stale lock (dead pid or older than the
    stale window) is stolen with a warning. Concurrent claimants get the
    exception, never a wait - the product's answer to "already encoding"
    is a clear no, not a queue.

    Args:
        output_dir (Path): The output directory to guard.
        holder (str): A short label naming the encoder (job/watch/CLI).

    Returns:
        Path: The lock file path (release_encode_slot takes it back).

    Raises:
        EncodeBusyError: When another live encoder holds the slot.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(output_dir)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # someone holds it: stale check
        try:
            age = time.time() - lock.stat().st_mtime
            raw = lock.read_text(encoding="ascii").strip()
            pid = int(raw) if raw.isdigit() else 0
        except (OSError, ValueError):
            age, pid = _LOCK_STALE_SECONDS + 1, 0  # unreadable => treat stale
        if pid and _pid_alive(pid) and age < _LOCK_STALE_SECONDS:
            raise EncodeBusyError(
                f"another encoder is already using '{output_dir}' "
                f"(pid {pid}, started {age / 60:.0f} min ago)"
            ) from None
        log_warning(f"Stale encode lock in '{output_dir}' (pid {pid or '?'}) - stealing it.")
        _release_path(lock)
        return acquire_encode_slot(output_dir, holder)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(str(os.getpid()))
    return lock


def release_encode_slot(lock: Path) -> None:
    """Releases a held encode slot.

    Args:
        lock (Path): The lock file path returned by acquire_encode_slot.
    """
    _release_path(lock)


def _release_path(lock: Path) -> None:
    """Removes a lock file, tolerating races and missing files.

    Args:
        lock (Path): The lock file to remove.
    """
    with contextlib.suppress(OSError):
        lock.unlink()


@contextlib.contextmanager
def guard_encodes(output_dir: Path, holder: str = "encode"):
    """Context manager around a whole encode run (all entry points).

    Args:
        output_dir (Path): The output directory the run writes into.
        holder (str): A short label naming the encoder (job/watch/CLI).

    Yields:
        Path: The lock file path.
    """
    lock = acquire_encode_slot(output_dir, holder)
    try:
        yield lock
    finally:
        release_encode_slot(lock)
