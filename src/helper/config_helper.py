# this script loads all the config data and ships it to the other scripts => provides default values
import json

Config = None

def read_config(root):
    # get root path of project
    with open(str(root) + '\\config.json', 'r', encoding="utf-8") as config:
       test = json.load(config)

    global Config
    Config = test
    

def check_config(root):
    global Config
    if Config == None:
        read_config(root)


def get_blackbar_settings(root):
    # first of all check if config is in global space
    check_config(root)

    global Config

    if Config != None:
        width = Config.get('ffmpeg', {}).get('black_bars', {}).get('MIN_WIDTH', None)
        height = Config.get('ffmpeg', {}).get('black_bars', {}).get('MIN_HEIGHT', None)
        # returns a dict
        return {'width': width, 'height': height}


# tests

def test_config(root):
    msg = "Does not exist"
    status = "ERROR"
    check_config(root)
    if Config != None:
        msg = "Does exist"
        status = "OK"

    return {'status': status, "message": msg}    

