# this module loads the config.json file and provides the settings to the other modules => provides default values
import json
import os
from pathlib import Path
from typing import Any

from shrinkray.logger import log, log_warning

# fallback values if the config file is missing or a value is not set
DEFAULT_EXTENSIONS = [".mkv"]
DEFAULT_SEPARATOR = ";"
DEFAULT_BLACK_BAR_MIN_WIDTH = 100
DEFAULT_BLACK_BAR_MIN_HEIGHT = 100
DEFAULT_LANGUAGE = "en"
DEFAULT_MAX_THREADS = 0  # 0 = use all cores
DEFAULT_IDLE_PRIORITY = True
# quality mode's encoder speed preset (x264/x265 ladder; see #21 for AV1)
# quality mode's encoder speed presets (x264/x265 ladder; see #21 for AV1).
# TWO presets since #22: the PROBE preset is a measurement setting (the CRF
# search's throwaway encodes), the FINAL preset is an output setting (the kept
# artifact). They are settable - and defaulted - separately on purpose.
DEFAULT_PROBE_ENCODER_PRESET = "medium"
DEFAULT_ENCODER_PRESET = "slow"
# the speed-preset vocabulary of the x264/x265 encoders (HandBrake passes the
# value through to --encoder-preset; anything else is rejected at load, never
# misparsed by HandBrake)
ENCODER_PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
    "placebo",
)
# per-encoder preset vocabularies and defaults (issue #22: the issue's numeric
# defaults 5/3 are SVT-AV1 vocabulary; x265 takes names). The SVT-AV1 entry is
# the shape #21 will activate; no encoder selects it yet.
ENCODER_PRESET_VOCABULARIES: dict[str, tuple[str, ...]] = {
    "x264": ENCODER_PRESETS,
    "x265": ENCODER_PRESETS,
    "x265_10bit": ENCODER_PRESETS,
    "svt_av1": tuple(str(n) for n in range(-1, 11)),
}
ENCODER_PRESET_DEFAULTS: dict[str, tuple[str, str]] = {
    # encoder => (probe preset, final preset)
    "x264": (DEFAULT_PROBE_ENCODER_PRESET, DEFAULT_ENCODER_PRESET),
    "x265": (DEFAULT_PROBE_ENCODER_PRESET, DEFAULT_ENCODER_PRESET),
    "x265_10bit": (DEFAULT_PROBE_ENCODER_PRESET, DEFAULT_ENCODER_PRESET),
    "svt_av1": ("5", "3"),
}
# stall watchdog: minutes of child silence before termination (0 = off)
DEFAULT_STALL_MINUTES = 10.0
# eligibility: sources below this height are never re-encoded (0 = gate off).
# PRODUCT.md makes 1080p the floor: a lower-resolution source has too few
# pixels to save meaningfully, so the probe cost is spent for nothing
DEFAULT_MIN_SOURCE_HEIGHT = 1080
# cropdetect windows per movie: more samples can only shrink the crop once
# the bars are the ones common to every sample, which is the safe direction
# for an irreversible crop (issue #17)
DEFAULT_CROP_SAMPLE_COUNT = 10
# probe density as a duration: one window per N seconds of runtime, so
# coverage is a fixed fraction of the file instead of shrinking with length
DEFAULT_SAMPLE_EVERY = 600.0
DEFAULT_SAMPLE_COUNT = 3
DEFAULT_SAMPLE_SECONDS = 10.0
# eligibility: sources below this size are never re-encoded (0 = gate off).
# Probe cost is roughly constant per file while the payoff scales with size,
# so the smallest files have the worst cost/benefit ratio
DEFAULT_MIN_SOURCE_SIZE_GB = 0.0
# eligibility: the source codecs that are candidates on size alone. A modern
# codec (HEVC/AV1) is already efficient, so it is a candidate only when it has
# bars to remove; anything older is never re-encoded (PRODUCT.md)
DEFAULT_CANDIDATE_CODECS = ["h264"]


class Conf:
    """Provides typed access to the config.json settings.

    All values fall back to sane defaults if they are missing in the config file.
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}

        # codec => hdr detection
        codec: dict[str, Any] = data.get("codec") or {}
        hdr: dict[str, Any] = codec.get("hdr") or {}
        self.hdr_color_spaces: list[str] = hdr.get("color_space") or []
        self.hdr_color_transfers: list[str] = hdr.get("color_transfer") or []

        # file => extensions and output settings
        file: dict[str, Any] = data.get("file") or {}
        self.extensions: list[str] = file.get("extensions") or DEFAULT_EXTENSIONS
        output: dict[str, Any] = file.get("output") or {}
        self.separator: str = output.get("seperator", DEFAULT_SEPARATOR)

        # ffmpeg => black bar detection
        ffmpeg: dict[str, Any] = data.get("ffmpeg") or {}
        black_bars: dict[str, Any] = ffmpeg.get("black_bars") or {}
        self.black_bar_min_width: int = int(
            black_bars.get("MIN_WIDTH", DEFAULT_BLACK_BAR_MIN_WIDTH)
        )
        self.black_bar_min_height: int = int(
            black_bars.get("MIN_HEIGHT", DEFAULT_BLACK_BAR_MIN_HEIGHT)
        )

        self.language: str = data.get("language", DEFAULT_LANGUAGE)

        # handbrake => re-encoding settings
        handbrake: dict[str, Any] = data.get("handbrake") or {}
        self.handbrake_preset: str = handbrake.get("preset", "Fast 1080p30")
        self.handbrake_encoder: str = handbrake.get("encoder", "x265")
        # quality mode's TWO encoder speed presets (issue #22): the probe
        # preset drives the CRF search's throwaway measurement encodes, the
        # final preset drives the kept artifact - measurement at 'medium',
        # artifact at 'slow' by default. The probe is a PREDICTION of the
        # final encode: preset shifts the CRF-to-VMAF curve, so a probe at a
        # different preset than the final encode measures a different curve
        # - a deliberate trade-off (documented in the README), not a bug.
        # Distinct from handbrake.preset above: that one is the FIXED-mode
        # HandBrake preset NAME (e.g. 'Fast 1080p30'); these are per-encode
        # speed presets of the quality search. Validated against the active
        # encoder's vocabulary (invalid values are a load-time error, never
        # passed to HandBrake - the SVT-AV1 numeric vocabulary applies when
        # #21 lands).
        self.handbrake_probe_encoder_preset: str = handbrake.get(
            "probe_encoder_preset", DEFAULT_PROBE_ENCODER_PRESET
        )
        self.handbrake_encoder_preset: str = handbrake.get("encoder_preset", DEFAULT_ENCODER_PRESET)
        for key, value in (
            ("probe_encoder_preset", self.handbrake_probe_encoder_preset),
            ("encoder_preset", self.handbrake_encoder_preset),
        ):
            if value not in ENCODER_PRESETS:
                raise ValueError(
                    f"handbrake.{key}: '{value}' is not a "
                    f"valid speed preset for encoder '{self.handbrake_encoder}' "
                    f"(valid: {', '.join(ENCODER_PRESETS)})"
                )
        # thread cap for the encoder (0 = use all cores); keeps cores free
        # for a media server running next to the encode on a small machine
        self.handbrake_max_threads: int = int(handbrake.get("max_threads", DEFAULT_MAX_THREADS))
        # stall watchdog: minutes without child output before a hung
        # HandBrake/ffmpeg is terminated (0 = disabled; default 10)
        self.handbrake_stall_minutes: float = float(
            handbrake.get("stall_minutes", DEFAULT_STALL_MINUTES)
        )

        # ffmpeg => crop detection
        self.crop_sample_count: int = int(
            ffmpeg.get("crop_sample_count", DEFAULT_CROP_SAMPLE_COUNT)
        )
        if self.crop_sample_count < 1:
            raise ValueError(
                f"ffmpeg.crop_sample_count: {self.crop_sample_count} must be at least 1"
            )

        # quality => probe density
        quality: dict[str, Any] = data.get("quality") or {}
        self.sample_every: float = float(quality.get("sample_every", DEFAULT_SAMPLE_EVERY))
        self.sample_count: int = int(quality.get("sample_count", DEFAULT_SAMPLE_COUNT))
        self.sample_seconds: float = float(quality.get("sample_seconds", DEFAULT_SAMPLE_SECONDS))
        if self.sample_every < 0:
            raise ValueError(
                f"quality.sample_every: {self.sample_every} is negative "
                "(0 disables the density knob)"
            )
        if self.sample_count < 1:
            raise ValueError(f"quality.sample_count: {self.sample_count} must be at least 1")
        if self.sample_seconds <= 0:
            raise ValueError(f"quality.sample_seconds: {self.sample_seconds} must be positive")

        # resources => how politely the engine behaves on a shared machine
        resources: dict[str, Any] = data.get("resources") or {}
        self.idle_priority: bool = bool(resources.get("idle_priority", DEFAULT_IDLE_PRIORITY))

        # eligibility => sources below this height are never re-encoded
        # (0 = gate off). A lower-resolution source has too few pixels to
        # save meaningfully, so the probe cost is spent for nothing.
        eligibility: dict[str, Any] = data.get("eligibility") or {}
        self.min_source_height: int = int(
            eligibility.get("min_source_height", DEFAULT_MIN_SOURCE_HEIGHT)
        )
        if self.min_source_height < 0:
            raise ValueError(
                f"eligibility.min_source_height: {self.min_source_height} is negative "
                "(0 disables the gate)"
            )
        self.min_source_size_gb: float = float(
            eligibility.get("min_source_size_gb", DEFAULT_MIN_SOURCE_SIZE_GB)
        )
        if self.min_source_size_gb < 0:
            raise ValueError(
                f"eligibility.min_source_size_gb: {self.min_source_size_gb} is negative "
                "(0 disables the gate)"
            )
        self.candidate_codecs: list[str] = [
            str(c).lower()
            for c in (eligibility.get("candidate_codecs") or DEFAULT_CANDIDATE_CODECS)
        ]
        if not self.candidate_codecs:
            raise ValueError(
                "eligibility.candidate_codecs: the list is empty, so no source "
                "would ever be re-encoded"
            )

        # the integrations key (opt-in media-server hooks) was REMOVED with the
        # Jellyfin integration (issue #43): an operator who relied on the
        # refresh must see that it is gone, so a leftover key is named - once,
        # honestly, and without failing the load
        if "integrations" in data:
            log_warning(
                "config.json contains 'integrations' - the media-server "
                "integration was removed (issue #43); the key is ignored."
            )


_config: Conf | None = None

# the config file's resolved path (set by the last load; surfaced for logs
# and the dashboard's "which file is in effect" display)
config_file_used: Path | None = None

CONFIG_ENV_VAR = "SHRINKRAY_CONFIG"

DEFAULT_CONFIG_FILENAME = "config.json"


def resolve_config_path(
    config_file: str | Path | None = None,
    root: str | Path | None = None,
    db_dir: str | Path | None = None,
) -> Path:
    """Resolves which config.json is in effect (issue #58).

    One location, decided once per process, in this order:

    1. an explicit config FILE path (the CLI's --config option),
    2. an explicit root DIRECTORY (tests, the app's db-dir convention),
    3. the SHRINKRAY_CONFIG environment variable (the container/NAS
       mount point),
    4. db_dir/config.json - the directory the operator already chose for
       user data,
    5. ./config.json (the working directory; the default --db lives there
       too, so a repo-root run reads the repo's config.json).

    Package-relative lookup is gone deliberately: in an installed wheel the
    directory above site-packages is not a user-writable location, and the
    dashboard and the engine must agree on one file.

    Args:
        config_file (str | Path | None): Explicit config FILE path.
        root (str | Path | None): Explicit directory holding config.json.
        db_dir (str | Path | None): The database file's directory (the
            operator's chosen user-data location).

    Returns:
        Path: The config file path in effect.
    """
    if config_file is not None:
        return Path(config_file)
    if root is not None:
        return Path(root) / DEFAULT_CONFIG_FILENAME
    env = os.environ.get(CONFIG_ENV_VAR)
    if env:
        return Path(env)
    if db_dir is not None:
        return Path(db_dir) / DEFAULT_CONFIG_FILENAME
    return Path(DEFAULT_CONFIG_FILENAME)


def reset_config() -> None:
    """Drops the cached config so the next load_config() reads the file.

    The public way for the dashboard's PUT /api/config to swap the
    in-memory singleton (assigning config._config from outside the module
    reached into private state; issue #58).
    """
    global _config
    _config = None


def load_config(
    root: str | Path | None = None,
    config_file: str | Path | None = None,
) -> Conf:
    """Loads the config.json file (once) and returns the settings.

    If the config file does not exist, default values are used. The file's
    location follows resolve_config_path; the resolved path is logged once
    at load so a user can see which file is in effect.

    Args:
        root (str | Path | None): Explicit directory holding config.json
            (the app passes its db directory; tests pass temp dirs).
        config_file (str | Path | None): Explicit config FILE path (the
            CLI's --config option; wins over everything else).

    Returns:
        Conf: The config settings.
    """
    global _config, config_file_used
    if _config is not None:
        return _config

    path = resolve_config_path(config_file=config_file, root=root)

    data: dict[str, Any] = {}
    if path.is_file():
        with open(path, encoding="utf-8") as config:
            data = json.load(config)
        log(f"Using config {path}")
    else:
        log_warning(f"Config file not found at '{path}', using default values!")
    config_file_used = path

    _config = Conf(data)
    # the resource policy rides along: every entry point loads the config
    # once, so every entry point also inherits the same politeness
    from shrinkray.resources import apply as apply_resources

    apply_resources(_config)
    # the stall watchdog rides along the same way (encode.py imports config
    # nowhere else; a single hook keeps the knob effective for CLI, app and
    # watch alike)
    from shrinkray.encode import configure_stall

    configure_stall(_config.handbrake_stall_minutes)
    return _config
