"""Watch mode: automatically encode new movies arriving in a folder.

Polls an inbox directory for new movie files, waits until they are stable
(no longer growing => the download/copy finished), applies the same skip
logic as the encode/plan commands and encodes them (source stays untouched
=> the human stays in the loop for replacing library files).
"""

import os
import tempfile
import threading
import time
from pathlib import Path

from shrinkray.encode import (
    DISK_FREE_FLOOR_BYTES,
    HANDBRAKE_BINARY,
    MIN_ENCODE_SECONDS,
    cap_threads,
    disk_free_bytes,
    find_handbrake,
    probe_duration_seconds,
)
from shrinkray.encode_lock import EncodeBusyError, guard_encodes
from shrinkray.logger import log, log_error
from shrinkray.quality import DEFAULT_TARGET_VMAF, Cancelled, encode_with_quality

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

    Applies only the CHEAP pre-checks (duration minimum, binary presence);
    the skip decisions that need probing (no size gain, target unreachable)
    happen inside encode_with_quality, which reports them via
    skipped_reason. This used to run the full probe here and then discard
    the CRF - double probe cost for nothing.

    Args:
        movie (Path): The movie file.
        target_vmaf (float): The VMAF quality floor (unused here since the
            quality search owns the skip rules; kept for signature
            compatibility).
        remove_bars (bool): Unused here (same reason).
        encoder (str): The video encoder (unused here, same reason).

    Returns:
        str: 'encode' or a cheap skip reason.
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


def _sleep_or_stop(seconds: float, stop_event: threading.Event | None) -> None:
    """Sleeps up to seconds, returning early when the stop event fires.

    Args:
        seconds (float): The sleep duration.
        stop_event (threading.Event | None): Cooperative stop; when set,
            this returns immediately (the caller re-checks it).
    """
    if stop_event is not None:
        stop_event.wait(seconds)
    else:
        time.sleep(seconds)


def release_lock(lock_file: Path) -> None:
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
    encoder_preset: str = "medium",
    poll_interval: float = POLL_INTERVAL,
    once: bool = False,
    max_threads: int | None = None,
    stop_event: threading.Event | None = None,
    db_file: Path | None = None,
    min_source_height: int = 0,
    min_source_size_gb: float = 0.0,
    candidate_codecs: tuple[str, ...] = ("h264",),
    crop_sample_count: int = 10,
) -> None:
    """Watches the inbox and encodes newly arrived movies.

    This function blocks (unless once=True); stop it with Ctrl+C - the
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
        once (bool): One pass and return (for tests).
        max_threads (int | None): Per-encode thread cap (None/0 = all cores).
        stop_event (threading.Event | None): Cooperative stop for the resident
            app (checked between passes; the running encode finishes first).
        db_file (Path | None): The sqlite database file - enables the stall
            watchdog's failure-bundle telemetry (None: no telemetry; the
            CLI one-shot watch prints the reason instead).
    """
    extensions = [".mkv", ".mp4", ".avi", ".m4v", ".ts"]
    known: set[str] = set()
    lock_file = Path(tempfile.gettempdir()) / "shrinkray_watch.lock"
    sleep_interval = poll_interval

    # misconfiguration guard: when the output lands inside the watched
    # inbox, the next pass sees it as a new movie and re-encodes - for
    # inbox == output_dir that means HandBrake writes its own INPUT's path
    # (truncating the source mid-read, then A1 debris-removal on it).
    # Overlap in either direction poisons the candidate set => refuse.
    inbox_resolved = inbox.resolve()
    output_resolved = output_dir.resolve()
    if (
        inbox_resolved == output_resolved
        or inbox_resolved in output_resolved.parents
        or (output_resolved in inbox_resolved.parents)
    ):
        raise ValueError(
            "inbox and output_dir must be separate directories (the output "
            f"must not live inside the inbox): inbox='{inbox}', output='{output_dir}'"
        )

    if not acquire_lock(lock_file):
        log_error("Another watcher seems to be running (lock file exists).")
        return

    inbox.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                log("Watch stopped (stop event set).")
                return
            # pass-level disk guard: a full output drive would burn a full
            # CRF probe per candidate per pass (probe, watchdog cancel,
            # retry next pass, repeat). Skipping the pass BEFORE anything
            # enters `known` keeps the movies eligible for when space frees
            # up, and the adaptive poll backs off meanwhile.
            free = disk_free_bytes(output_dir)
            if free is not None and free < DISK_FREE_FLOOR_BYTES:
                log_error(
                    f"Output drive nearly full: {free / (1024**3):.1f} GB free "
                    f"(floor 1.0 GB) => waiting. Movies stay eligible; the "
                    "watcher retries when space frees up."
                )
                if once:
                    return
                _sleep_or_stop(sleep_interval, stop_event)
                continue
            candidates = find_new_movies(inbox, extensions, known)
            for movie in candidates:
                if stop_event is not None and stop_event.is_set():
                    log("Watch stopped (stop event set).")
                    return
                if not is_stable(movie):
                    # NOT marked known: the file is still copying and stays
                    # eligible (it must be reconsidered once stable - the
                    # old code added it to `known` first and never looked
                    # at it again, starving every normal copy arrival)
                    log(f"'{movie.name}' is not stable yet (still copying?) => later.")
                    continue
                known.add(str(movie.resolve()))
                decision = decide(movie, target_vmaf, remove_bars, encoder)
                if decision != "encode":
                    log(f"Skipping '{movie.name}': {decision}.")
                    continue
                # quality mode, same engine path as the CLI and jobs: the
                # CRF search decides skip-vs-encode (no size gain, target
                # unreachable) and reports it via skipped_reason. Watch used
                # to encode a fixed preset here - the inbox automation
                # produced worse files than the manual path.
                try:
                    # hold the encode slot: encode_with_quality does not
                    # take it itself; a running CLI/job encode makes the
                    # watcher retry this movie on the next pass
                    with guard_encodes(output_dir, holder="watch"):
                        result = encode_with_quality(
                            movie,
                            output_dir / movie.name,
                            target_vmaf=target_vmaf,
                            remove_bars=remove_bars,
                            encoder=encoder,
                            encoder_preset=encoder_preset,
                            encoder_threads=cap_threads(max_threads),
                            db_file=db_file,
                            # inbox automation must not silently depend on
                            # plan staleness: watch always measures fresh
                            force_reprobe=True,
                            min_source_height=min_source_height,
                            min_source_size_gb=min_source_size_gb,
                            candidate_codecs=candidate_codecs,
                            crop_sample_count=crop_sample_count,
                        )
                except EncodeBusyError as e:
                    log(f"Skipping '{movie.name}' for now: {e} => retry next pass.")
                    known.discard(str(movie.resolve()))  # eligible again next pass
                    continue
                except Cancelled as e:
                    # disk watchdog (free space hit the floor) or user cancel:
                    # partial output already removed by the engine; retry
                    # when space freed up (or the user re-ran watch)
                    log_error(f"Encoding '{movie.name}' cancelled: {e} => retry next pass.")
                    known.discard(str(movie.resolve()))
                    continue
                if result.skipped_reason:
                    log(f"Skipping '{movie.name}': {result.skipped_reason}.")
                elif result.success:
                    log(
                        f"Done: '{movie.name}' -> CRF {result.chosen_crf} "
                        f"(VMAF {result.achieved_vmaf}), "
                        f"{result.saved_percent:.1f}% saved (source left untouched)."
                    )
                else:
                    log_error(f"Encoding '{movie.name}' failed: {result.error}")

            sleep_interval = next_poll_interval(sleep_interval, poll_interval, bool(candidates))
            if once:
                return
            _sleep_or_stop(sleep_interval, stop_event)
    except KeyboardInterrupt:
        log("Watch stopped (current state is kept).")
    finally:
        release_lock(lock_file)
