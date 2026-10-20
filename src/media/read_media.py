from decimal import Decimal, InvalidOperation
from pathlib import Path

import ffmpeg
import gc
import math

try:
    from src.helper.config_helper import load_config
except ImportError:
    from helper.config_helper import load_config


def get_aspect_ratio(width, height) -> str:
    gcd = math.gcd(width, height)
    lhs = int(width / gcd)
    rhs = int(height / gcd)
    return f"{lhs}x{rhs}"


def parse_frame_rate(rate: str | None) -> float | None:
    """Parses a ffmpeg frame rate string like '25/1' or '29.97' into a float.

    Args:
        rate (str | None): Frame rate string, e.g. '25/1'.

    Returns:
        float | None: The frame rate as float, or None if it cannot be parsed.
    """
    if not rate or rate == '0/0':
        return None
    try:
        if '/' in rate:
            numerator, denominator = rate.split('/', 1)
            numerator = float(numerator)
            denominator = float(denominator)
            if denominator == 0:
                return None
            return numerator / denominator
        return float(rate)
        # division by zero / invalid strings are caught below
    except (ValueError, ZeroDivisionError):
        return None


def get_raw_duration(video) -> float | None:
    """Calculates the raw duration from the frame count and frame rate.

    Args:
        video: The video stream data from ffmpeg.probe().

    Returns:
        float | None: The duration in seconds, or None if it cannot be calculated.
    """
    duration_raw = None
    tags = video.get('tags') or {}
    frame_rate = parse_frame_rate(video.get('avg_frame_rate'))

    # frame count can be stored in different tags depending on the file/muxer
    frame_count = None
    for key in ('NUMBER_OF_FRAMES-eng', 'NUMBER_OF_FRAMES', 'nb_frames'):
        if key in tags:
            frame_count = tags[key]
            break

    if frame_count is None and 'nb_frames' in video:
        frame_count = video['nb_frames']

    if frame_count is not None and frame_rate:
        try:
            duration_raw = float(frame_count) / frame_rate
        except (TypeError, ValueError):
            return None
    return duration_raw


def get_duration(video, language: str = 'en') -> str | None:
    """Gets the duration from the stream tags, preferring the configured language.

    Args:
        video: The video stream data from ffmpeg.probe().
        language (str): Preferred language for the DURATION tags, e.g. 'en'.

    Returns:
        str | None: The duration as 'HH:MM:SS' string, or None if not present.
    """
    duration = None
    tags = video.get('tags') or {}
    try:
        # search for the configured language first, then fallbacks
        keys = [f'DURATION-{language}', 'DURATION-eng',
                'DURATION-de', 'DURATION']
        for key in keys:
            if key in tags:
                duration = tags[key].split('.')[0]
                break
        else:
            # no duration tag => fallback to the format duration
            return None
    except (AttributeError, KeyError, TypeError):
        return duration
    return duration


def seconds_to_timestr(seconds: float) -> str:
    """Converts seconds into a 'HH:MM:SS' string.

    Args:
        seconds (float): The duration in seconds.

    Returns:
        str: The duration as 'HH:MM:SS' string.
    """
    seconds = int(round(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def try_get_width(video):
    width = None
    if 'width' in video:
        width = video['width']
    elif 'coded_width' in video:
        width = video['coded_width']
    return width


def try_get_height(video):
    height = None
    if 'height' in video:
        height = video['height']
    elif 'coded_height' in video:
        height = video['coded_height']
    return height


def get_video_stream(streams) -> dict | None:
    """Finds the first video stream in the probe data.

    Args:
        streams: The streams list from ffmpeg.probe().

    Returns:
        dict | None: The video stream, or None if there is none.
    """
    for stream in streams:
        if stream.get('codec_type') == 'video':
            return stream
    return None


def get_codec_info(stream) -> dict:
    """Extracts codec name, type, language and title from a stream.

    Args:
        stream: A stream from ffmpeg.probe().

    Returns:
        dict: Dictionary with 'name', 'type', 'language' and 'title'.
    """
    codec_name = stream.get('codec_name', '')
    codec_type = stream.get('codec_type', '')
    codec_language: str = ''
    codec_title: str = ''
    tags = stream.get('tags') or {}
    codec_language = tags.get('language', '')
    codec_title = tags.get('title', '')
    return {"name": str(codec_name), "type": codec_type,
            "language": codec_language, "title": codec_title}


def get_audio_languages(streams) -> str:
    """Collects the languages of all audio streams as comma separated string.

    Args:
        streams: The streams list from ffmpeg.probe().

    Returns:
        str: Comma separated languages, e.g. 'de, en'. Empty if there are none.
    """
    languages: list[str] = []
    for stream in streams:
        if stream.get('codec_type') != 'audio':
            continue
        language = (stream.get('tags') or {}).get('language', '')
        if language and language not in languages:
            languages.append(language)
    return ', '.join(languages)


def is_hdr(video, config) -> bool:
    """Checks if the video stream is HDR by comparing its color data with the config.

    Args:
        video: The video stream data from ffmpeg.probe().
        config (Conf): The config settings.

    Returns:
        bool: True if the video is HDR.
    """
    color_space = video.get('color_space', '')
    color_transfer = video.get('color_transfer', '')

    for space in config.hdr_color_spaces:
        if space and space in str(color_space):
            return True
    for transfer in config.hdr_color_transfers:
        if transfer and transfer in str(color_transfer):
            return True
    return False


def get_data(path: Path, config=None) -> dict:
    """Reads the metadata of a media file.

    Args:
        path (Path): Path to the media file.
        config (Conf | None): The config settings. Loaded if not given.

    Returns:
        dict: Dictionary with the metadata of the file.
    """
    if config is None:
        config = load_config()

    # read the audio/video file => uses ffprobe command to extract all possible metadata
    media_file = str(path)
    probe = ffmpeg.probe(media_file)

    bitrate = 0.00
    if 'format' in probe and probe['format'].get('bit_rate'):
        bitrate = round(
            Decimal(probe['format']['bit_rate'])/(1024*1024), 2)

    streams = probe.get('streams') or []
    video = get_video_stream(streams)
    if video is None:
        raise TypeError(f"No video stream found in '{media_file}'!")

    codec = video.get('codec_name', 'unknown')

    # for other codecs => needs to be included in the output file!
    other_codecs: list[dict] = []
    for stream in streams:
        if stream is video:
            continue
        other_codecs.append(get_codec_info(stream))

    # FPS (rounded to 2 decimals), '25' instead of '25.0'
    fps = parse_frame_rate(video.get('avg_frame_rate'))
    if fps:
        fps = round(fps, 2)
        fps = int(fps) if fps.is_integer() else fps

    duration = get_duration(video, config.language)
    duration_raw = get_raw_duration(video)

    # fallback: no duration tag => calculate 'HH:MM:SS' from the raw duration
    if duration is None and duration_raw is not None:
        duration = seconds_to_timestr(duration_raw)

    height = try_get_height(video)
    width = try_get_width(video)

    # might look for a better option => 16:9 - excel will convert this to datetime
    aspect_ratio = '0x0'
    if width is not None and height is not None:
        aspect_ratio = get_aspect_ratio(width, height)

    audio_languages = get_audio_languages(streams)
    hdr = is_hdr(video, config)

    # clear data
    del streams, video
    gc.collect()

    return {"codec": codec, "other_codecs": other_codecs, "bitrate": bitrate,
            "duration": duration, "duration_raw": duration_raw, "fps": fps,
            "aspect_ratio": aspect_ratio,
            "dimensions": {"width": width, "height": height},
            "audio_languages": audio_languages, "hdr": hdr}
