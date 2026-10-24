"""plan: the stored plan's persisted shape (issue #97 phase 3, seam 4).

Moved verbatim from quality.py: the readers that rebuild a stored plan
row into live objects (the crop rect, the probe list, the fingerprint
lookup). plan_quality itself moves with the pipeline seam (it returns
QualityResult, which lands in pipeline).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from shrinkray.engine.crop import DEFAULT_CROP_SAMPLE_COUNT
from shrinkray.engine.search import VMAF_STATISTIC, SampleProbe


def _plan_crop(plan: sqlite3.Row) -> tuple[int, int, int, int] | None:
    """Parses a stored plan's crop rect ('top,bottom,left,right').

    Args:
        plan (sqlite3.Row): The plan row.

    Returns:
        tuple[int, int, int, int] | None: The crop rect, or None.
    """
    raw = plan["crop"]
    if not raw:
        return None
    parts = [int(v) for v in str(raw).split(",")]
    if len(parts) != 4:
        return None
    return (parts[0], parts[1], parts[2], parts[3])


def _plan_probes(plan: sqlite3.Row) -> list[SampleProbe]:
    """Restores a stored plan's probe list from its JSON column.

    Args:
        plan (sqlite3.Row): The plan row.

    Returns:
        list[SampleProbe]: The probes that backed the plan's decision.
    """
    try:
        pairs = json.loads(plan["probes"] or "[]")
    except (TypeError, ValueError):
        return []
    return [SampleProbe(crf=int(c), vmaf=float(v)) for c, v in pairs]


def _lookup_plan(
    db_file: Path,
    input_file: Path,
    *,
    encoder: str,
    encoder_preset: str,
    target_vmaf: float,
    crf_range: tuple[int, int],
    sample_count: int,
    sample_seconds: float,
    crop_sample_count: int = DEFAULT_CROP_SAMPLE_COUNT,
) -> sqlite3.Row | None:
    """Consults the plan cache for a valid plan of this movie+settings.

    Opens a short-lived read connection (the engine stays connection-free;
    db_file already locates the logs dir, so it already knows the db).
    Any mismatch, missing file row or vanished movie returns None — the
    caller re-probes, never guesses.

    Args:
        db_file (Path): The database file.
        input_file (Path): The movie to encode.
        encoder (str): The pending encode's effective encoder.
        encoder_preset (str): The pending encode's preset.
        target_vmaf (float): The pending encode's (HDR-overridden) target.
        crf_range (tuple[int, int]): The pending encode's (HDR-overridden) range.
        sample_count (int): The pending encode's sample count.
        sample_seconds (float): The pending encode's sample length.
        crop_sample_count (int): The pending encode's crop sample count.

    Returns:
        sqlite3.Row | None: The valid plan row, or None.
    """
    from shrinkray import db as _db

    if not input_file.is_file():
        return None
    connection = _db.connect(db_file)
    try:
        return _db.get_valid_plan(
            connection,
            input_file,
            encoder=encoder,
            encoder_preset=encoder_preset,
            target_vmaf=target_vmaf,
            crf_range=crf_range,
            sample_count=sample_count,
            sample_seconds=sample_seconds,
            crop_sample_count=crop_sample_count,
            vmaf_statistic=VMAF_STATISTIC,
        )
    finally:
        connection.close()
