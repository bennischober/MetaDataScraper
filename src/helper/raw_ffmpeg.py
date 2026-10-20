"""Helper to dump the ffmpeg probe data of a media file as json files."""
import sys
import json

import ffmpeg


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m src.helper.raw_ffmpeg <path-to-media-file>")
        raise SystemExit(1)

    media_file = sys.argv[1]
    vid = ffmpeg.probe(media_file)["streams"]
    vid_raw = ffmpeg.probe(media_file)

    with open('movie_info.json', 'w+', encoding='utf-8') as text:
        text.write(json.dumps(vid, indent=2))

    with open('movie_info_raw.json', 'w+', encoding='utf-8') as raw_text:
        raw_text.write(json.dumps(vid_raw, indent=2))

    print(f"Written movie_info.json and movie_info_raw.json for '{media_file}'.")


if __name__ == "__main__":
    main()
