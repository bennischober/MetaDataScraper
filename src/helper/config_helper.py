# this module loads the config.json file and provides the settings to the other modules => provides default values
import json
from pathlib import Path
from typing import Any

try:
    from src.helper.logger import log_warning
except ImportError:
    from helper.logger import log_warning

# fallback values if the config file is missing or a value is not set
DEFAULT_EXTENSIONS = ['.mkv']
DEFAULT_SEPARATOR = ';'
DEFAULT_BLACK_BAR_MIN_WIDTH = 100
DEFAULT_BLACK_BAR_MIN_HEIGHT = 100
DEFAULT_LANGUAGE = 'en'


class Conf:
    """Provides typed access to the config.json settings.

    All values fall back to sane defaults if they are missing in the config file.
    """

    def __init__(self, data: dict[str, Any] | None = None):
        data = data or {}

        # codec => hdr detection
        codec: dict[str, Any] = data.get('codec') or {}
        hdr: dict[str, Any] = codec.get('hdr') or {}
        self.hdr_color_spaces: list[str] = hdr.get('color_space') or []
        self.hdr_color_transfers: list[str] = hdr.get('color_transfer') or []

        # file => extensions and output settings
        file: dict[str, Any] = data.get('file') or {}
        self.extensions: list[str] = file.get('extensions') or DEFAULT_EXTENSIONS
        output: dict[str, Any] = file.get('output') or {}
        self.separator: str = output.get('seperator', DEFAULT_SEPARATOR)

        # ffmpeg => black bar detection
        ffmpeg: dict[str, Any] = data.get('ffmpeg') or {}
        black_bars: dict[str, Any] = ffmpeg.get('black_bars') or {}
        self.black_bar_min_width: int = int(
            black_bars.get('MIN_WIDTH', DEFAULT_BLACK_BAR_MIN_WIDTH))
        self.black_bar_min_height: int = int(
            black_bars.get('MIN_HEIGHT', DEFAULT_BLACK_BAR_MIN_HEIGHT))

        self.language: str = data.get('language', DEFAULT_LANGUAGE)

    def print(self):
        print(self.hdr_color_spaces)
        print(self.hdr_color_transfers)
        print(self.extensions)
        print(self.separator)
        print(self.black_bar_min_width)
        print(self.black_bar_min_height)
        print(self.language)


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
    path = path / 'config.json'

    data: dict[str, Any] = {}
    if path.is_file():
        with open(path, 'r', encoding='utf-8') as config:
            data = json.load(config)
    else:
        log_warning(f"Config file not found at '{path}', using default values!")

    _config = Conf(data)
    return _config


def test_config(root: str | Path):
    """Checks if the config file exists and loads it.

    Args:
        root (str | Path): Root path of the project.

    Returns:
        dict: Dictionary with the 'status', 'message' and 'config' keys.
    """
    msg = "Does not exist"
    status = "ERROR"
    path = Path(root) / 'config.json'
    if path.is_file():
        msg = "Does exist"
        status = "OK"

    conf = load_config(root)

    return {'status': status, 'message': msg, 'config': conf}
