import enum
from typing import Any


class TEST_TYPE(enum.Enum):
    TEST = 0
    CONFIG = 1
    MEDIAREADER = 2


class PRINT_TYPE(enum.Enum):
    WARNING = 0
    ERROR = 1
    OK = 2
    NORMAL = 3
    BOLD = 4
    UNDERLINE = 5
    END = 6


class logcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ERROR = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def testlog(testtype: TEST_TYPE, message: dict[str, Any]):
    """Logs a message depending on the test type.

    Args:
        testtype (TEST_TYPE): The type of the test log.
        message (dict): Message type of dict: {'status', 'message'}.
    """
    match testtype:
        case TEST_TYPE.TEST:
            msg = message.get('message', None)
            if msg is None:
                return
            _log_("[TESTS]", str(msg), logcolors.BOLD)
        case TEST_TYPE.CONFIG:
            status = message.get('status', None)
            msg = message.get('message', None)
            if status is None or msg is None:
                return None
            if status == 'OK':
                _log_("[CONFIG]", "(success) " + str(msg), logcolors.OKGREEN)
            else:
                _log_("[CONFIG]", "(failed) " + str(msg), logcolors.FAIL)


def _log_(type: str | None = None, message: str | None = None, color: str | None = None):
    if message is None or color is None:
        return
    if type is None:
        print(color + message + logcolors.ENDC)
    else:
        print(type, color + message + logcolors.ENDC)


def log_call(message: str, type: PRINT_TYPE):
    col = None
    match type:
        case PRINT_TYPE.WARNING:
            col = logcolors.WARNING
        case PRINT_TYPE.ERROR:
            col = logcolors.FAIL
        case PRINT_TYPE.OK:
            col = logcolors.OKGREEN
        case PRINT_TYPE.NORMAL:
            col = logcolors.HEADER
        case PRINT_TYPE.BOLD:
            col = logcolors.BOLD
        case PRINT_TYPE.UNDERLINE:
            col = logcolors.UNDERLINE
        case PRINT_TYPE.END:
            col = logcolors.ENDC
    _log_(message=str(message), color=col)


def log(message: str):
    log_call(message, PRINT_TYPE.NORMAL)


def log_warning(message: str):
    log_call(message, PRINT_TYPE.WARNING)


def log_error(message: str):
    log_call(message, PRINT_TYPE.ERROR)


def log_multiline(messages: list[dict[str, Any]]):
    """Logs multiple messages.

    Args:
        messages (list): Messages of type {'msg': str, 'type': PRINT_TYPE}.
    """
    for msg in messages:
        log_call(msg['msg'], msg['type'])
