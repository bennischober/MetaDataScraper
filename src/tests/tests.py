from pathlib import Path

# import from /src/
try:
    from helper import config_helper
    from helper import logger
except ImportError:
    import sys
    sys.path.append(sys.path[0] + '/..')
    from helper import config_helper
    from helper import logger


def run_test():
    logger.testlog(logger.TEST_TYPE.TEST, {'status': 'OK', 'message': 'Starting tests!'})
    # run config test
    logger.testlog(logger.TEST_TYPE.CONFIG, config_helper.test_config(Path(__file__).parent.parent.parent))

    logger.testlog(logger.TEST_TYPE.CONFIG, {'status': 'ERROR', 'message': 'Config JSON'})

def main():
    run_test()

if __name__ == "__main__":
    main()
