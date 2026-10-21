"""Watch mode: automatically encode new movies arriving in a folder.

Polls an inbox directory for new movie files, waits until they are stable
(no longer growing => the download/copy finished), applies the same skip
logic as the encode/plan commands and encodes them (source stays untouched
=> the human stays in the loop for replacing library files).
"""

import os
import tempfile
import time
from pathlib import Path

from shrinkray.encode import (
    HANDBRAKE_BINARY,
    MIN_ENCODE_SECONDS,
    encode_movies,
    find_handbrake,
    probe_duration_seconds,
)
from shrinkray.logger import log, log_error
from shrinkray.quality import (
    DEFAULT_CRF_RANGE,
    DEFAULT_TARGET_VMAF,
    SkipEncode,
    _probe_movie,
)

# a file is stable when its size is unchanged between two checks ...
STABLE_CHECK_INTERVAL = 30.0
# ... and it is at least this old (grace period after the last modification)
STABLE_MIN_AGE = 60.0
POLL_INTERVAL = 60.0
# adaptive polling: while the inbox stays idle the sleep between polls grows
# up to this multiple of POLL_INTERVAL (any new candidate resets it back)
MAX_POLL_MULTIPLIER = 4.0


def next_poll_interval(current: float, base: float, found_new: bool) -> float:
    """Computes the next poll interval for the watch loop.

    The interval doubles while the inbox stays idle (fewer checks when
    nothing happens) and resets to the base interval as soon as a poll
    finds new candidates (activity suggests more may be coming).

    Args:
        current (float): The interval that was just used (seconds).
        base (float): The configured base interval (seconds).
        found_new (bool): Whether the last poll found new candidate files.

    Returns:
        float: The next interval in seconds: double the current one, capped
        at base * MAX_POLL_MULTIPLIER, or the base interval on activity.
    """
    if found_new:
        return base
    return min(current * 2.0, base * MAX_POLL_MULTIPLIER)


def find_new_movies(inbox: Path, extensions: list[str], known: set[str]) -> list[Path]:
    """Finds movie files in the inbox that are not in the known set.

    Args:
        inbox (Path): The directory to watch.
        extensions (list[str]): The extensions to search for.
        known (set[str]): Paths (as strings) that were already seen.

    Returns:
        list[Path]: The new movie files.
    """
    movies: list[Path] = []
    for extension in extensions:
        extension = extension.lower().lstrip(".")
        candidates = list(inbox.glob(f"*.{extension}"))
        candidates.extend(inbox.glob(f"*.{extension.upper()}"))
        movies.extend(candidates)
    # windows glob is case-insensitive => deduplicate, then filter known
    unique = list(dict.fromkeys(movies))
    return [m for m in unique if str(m.resolve()) not in known]


def is_stable(path: Path) -> bool:
    """Checks whether a file has finished growing (download/copy complete).

    A file is stable when its size does not change between two checks and
    it was last modified at least STABLE_MIN_AGE seconds ago.

    Args:
        path (Path): The file to check.

    Returns:
        bool: True if the file can be considered complete.
    """
    try:
        size_1 = path.stat().st_size
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    if age < STABLE_MIN_AGE:
        return False
    time.sleep(STABLE_CHECK_INTERVAL)
    try:
        size_2 = path.stat().st_size
    except OSError:
        return False
    return size_1 == size_2


def decide(movie: Path, target_vmaf: float, remove_bars: bool, encoder: str) -> str:
    """Decides what to do with a movie without encoding it.

    Applies the same rules as the encode command: duration minimum,
    skip-if-no-size-gain and skip-if-target-unreachable.

    Args:
        movie (Path): The movie file.
        target_vmaf (float): The VMAF quality floor.
        remove_bars (bool): Whether bar removal is enabled.
        encoder (str): The video encoder.

    Returns:
        str: 'encode' or the skip reason.
    """
    duration = probe_duration_seconds(movie)
    if duration is None:
        # unreadable/corrupt file (or missing ffprobe): let the encode path
        # decide => it reports the error properly instead of looping forever
        return "encode"
    if duration < MIN_ENCODE_SECONDS:
        return "too short"

    handbrake = find_handbrake()
    if handbrake is None:
        return f"'{HANDBRAKE_BINARY}' not found"

    try:
        _probe_movie(
            movie,
            target_vmaf,
            DEFAULT_CRF_RANGE,
            sample_count=3,
            sample_seconds=10.0,
            encoder=encoder,
            encoder_preset="medium",
            remove_bars=remove_bars,
        )
    except SkipEncode as e:
        message = str(e)
        return "target unreachable" if "unreachable" in message else "no size gain"
    except RuntimeError:
        # probing failed (e.g. corrupt media) => surface as encode attempt;
        # the encode itself will fail loudly and be reported
        return "encode"
    return "encode"


def acquire_lock(lock_file: Path) -> bool:
    """Tries to acquire the single-instance lock.

    Args:
        lock_file (Path): The lock file path.

    Returns:
        bool: True if the lock was acquired (this is the only watcher).
    """
    try:
        if lock_file.exists():
            # stale lock from a crashed run? a pid that does not exist anymore
            # frees the lock
            pid = int(lock_file.read_text(encoding="utf-8").strip() or 0)
            try:
                os.kill(pid, 0)
                return False  # process exists => another watcher is running
            except (OSError, ValueError):
                pass  # dead process or unparsable => take over
        lock_file.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError:
        return False


def release_lock(lock_file: Path):
    """Releases the single-instance lock if this process holds it.

    Args:
        lock_file (Path): The lock file path.
    """
    try:
        if lock_file.exists() and lock_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock_file.unlink()
    except OSError:
        pass


def watch(
    inbox: Path,
    output_dir: Path,
    target_vmaf: float = DEFAULT_TARGET_VMAF,
    remove_bars: bool = False,
    encoder: str = "x265",
    poll_interval: float = POLL_INTERVAL,
    once: bool = False,
):
    """Watches the inbox and encodes newly arrived movies.

    This function blocks (unless once=True); stop it with Ctrl+C — the
    running encode finishes first, then the state is written and the
    process exits cleanly.

    Args:
        inbox (Path): The directory to watch for new movies.
        output_dir (Path): Directory for the encoded files.
        target_vmaf (float): The VMAF quality floor.
        remove_bars (bool): Detect and crop stable black bars.
        encoder (str): The video encoder.
        poll_interval (float): Seconds between inbox checks (grows while the
            inbox stays idle, resets on any new candidate).
        once (bool): Run a single pass and return (used by tests).
    """
    extensions = [".mkv", ".mp4", ".avi", ".m4v", ".ts"]
    known: set[str] = set()
    lock_file = Path(tempfile.gettempdir()) / "shrinkray_watch.lock"
    sleep_interval = poll_interval

    if not acquire_lock(lock_file):
        log_error("Another watcher seems to be running (lock file exists).")
        return

    inbox.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        while True:
            candidates = find_new_movies(inbox, extensions, known)
            for movie in candidates:
                known.add(str(movie.resolve()))
                if not is_stable(movie):
                    log(f"'{movie.name}' is not stable yet (still copying?) => later.")
                    continue
                decision = decide(movie, target_vmaf, remove_bars, encoder)
                if decision != "encode":
                    log(f"Skipping '{movie.name}': {decision}.")
                    continue
                log(f"Encoding '{movie.name}'...")
                encode_movies(
                    [movie],
                    output_dir,
                    preset="Fast 1080p30",
                    encoder=encoder,
                    overwrite=False,
                )
                log(f"Done: '{movie.name}' (source left untouched).")

            sleep_interval = next_poll_interval(sleep_interval, poll_interval, bool(candidates))
            if once:
                return
            time.sleep(sleep_interval)
    except KeyboardInterrupt:
        log("Watch stopped (current state is kept).")
    finally:
        release_lock(lock_file)
