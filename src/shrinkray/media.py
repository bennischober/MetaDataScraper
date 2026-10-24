"""TRANSITIONAL RE-EXPORT SHIM (issue #97 phase 2, allowed until phase 6).

media.py moved verbatim to tools/ffprobe.py (issue #97: only tools/ffprobe.py
runs and parses ffprobe). Everything here re-exports the moved names so
in-flight branches and existing imports keep working. Import from
shrinkray.tools.ffprobe directly in NEW code; this shim is removed when the
import-linter contract lands (phase 6).
"""

from __future__ import annotations

from shrinkray.tools.ffprobe import (
    FFPROBE_TIMEOUT,
    get_aspect_ratio,
    get_audio_languages,
    get_bitrate,
    get_codec_info,
    get_data,
    get_duration,
    get_raw_duration,
    get_video_stream,
    is_hdr,
    parse_frame_rate,
    run_ffprobe,
    seconds_to_timestr,
    try_get_height,
    try_get_width,
)

__all__ = [
    "FFPROBE_TIMEOUT",
    "get_aspect_ratio",
    "get_audio_languages",
    "get_bitrate",
    "get_codec_info",
    "get_data",
    "get_duration",
    "get_raw_duration",
    "get_video_stream",
    "is_hdr",
    "parse_frame_rate",
    "run_ffprobe",
    "seconds_to_timestr",
    "try_get_height",
    "try_get_width",
]
