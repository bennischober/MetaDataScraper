import enum

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


def testlog(testtype, message):
    # message type of dict: {'status', 'message'}
    match testtype:
        case TEST_TYPE.TEST:
            msg = message.get('message', None)
            if msg is None:
                return
            _log_("[TESTS]", msg, logcolors.BOLD)
        case TEST_TYPE.CONFIG:
            status = message.get('status', None)
            msg = message.get('message', None)
            if status is None or msg is None:
                return None
            if status == 'OK':
                _log_("[CONFIG]", "(success) " + msg, logcolors.OKGREEN)
            else:
                _log_("[CONFIG]", "(failed) " + msg, logcolors.FAIL)

def _log_(type, message, color):
    if message == None or color == None:
        return
    if type is None:
        print(color + message + logcolors.ENDC)
    else:    
        print(type, color + message + logcolors.ENDC)

def log_call(message, type):
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
    _log_(message, col)

def log(message):
    log_call(message, PRINT_TYPE.NORMAL)

def log_warning(message):
    log_call(message, PRINT_TYPE.WARNING)

def log_error(message):
    log_call(message, PRINT_TYPE.ERROR)

def log_multiline(messages):
    # messages: {'msg', 'type'}
    for msg in messages:
        log_call(msg['msq'], msg['type'])