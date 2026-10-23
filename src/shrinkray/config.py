# this module loads the config.json file and provides the settings to the other modules => provides default values
import json
from pathlib import Path
from typing import Any

from shrinkray.logger import log_warning

# fallback values if the config file is missing or a value is not set
DEFAULT_EXTENSIONS = [".mkv"]
DEFAULT_SEPARATOR = ";"
DEFAULT_BLACK_BAR_MIN_WIDTH = 100
DEFAULT_BLACK_BAR_MIN_HEIGHT = 100
DEFAULT_LANGUAGE = "en"
DEFAULT_MAX_THREADS = 0  # 0 = use all cores
DEFAULT_IDLE_PRIORITY = True
# quality mode's encoder speed preset (x264/x265 ladder; see #21 for AV1)
DEFAULT_ENCODER_PRESET = "medium"
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
# stall watchdog: minutes of child silence before termination (0 = off)
DEFAULT_STALL_MINUTES = 10.0


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
        # quality mode's encoder SPEED preset (x264/x265 ladder; the SVT-AV1
        # numeric vocabulary replaces it when #21 lands). Distinct from
        # handbrake.preset above: that one is the FIXED-mode HandBrake preset
        # NAME (e.g. 'Fast 1080p30'); this one is the per-encode speed of the
        # quality search's CRF runs — two keys named 'preset' with different
        # meanings is a footgun, so the distinction is stated here (issue #31)
        self.handbrake_encoder_preset: str = handbrake.get("encoder_preset", DEFAULT_ENCODER_PRESET)
        if self.handbrake_encoder_preset not in ENCODER_PRESETS:
            raise ValueError(
                f"handbrake.encoder_preset: '{self.handbrake_encoder_preset}' is not a "
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

        # resources => how politely the engine behaves on a shared machine
        resources: dict[str, Any] = data.get("resources") or {}
        self.idle_priority: bool = bool(resources.get("idle_priority", DEFAULT_IDLE_PRIORITY))

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


def load_config(root: str | Path | None = None) -> Conf:
    """Loads the config.json file (once) and returns the settings.

    If the config file does not exist, default values are used.

    Args:
        root (str | Path | None): Root path of the project.
            Defaults to the project root (parent of the src directory).

    Returns:
        Conf: The config settings.
    """
    global _config
    if _config is not None:
        return _config

    path = Path(root) if root else Path(__file__).parent.parent.parent
    path = path / "config.json"

    data: dict[str, Any] = {}
    if path.is_file():
        with open(path, encoding="utf-8") as config:
            data = json.load(config)
    else:
        log_warning(f"Config file not found at '{path}', using default values!")

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
