import ffmpeg
from pprint import pprint
import sys
import json

# helper to check movie info
def main():
    dir = sys.argv[1]
    vid = ffmpeg.probe(dir)["streams"]
    # pprint(vid)

    with open('movie_info.json', 'w+') as text:
        text.write(json.dumps(vid))

if __name__ == "__main__":
    main()