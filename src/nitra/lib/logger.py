import logging
import os

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"
_warned_invalid_level = False

class LoggerFormatter(logging.Formatter):

    grey = "\x1b[38;21m"
    green = "\033[92m"
    yellow = "\x1b[33;21m"
    red = "\x1b[31;21m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    FORMATS = {
        logging.DEBUG: grey + LOG_FORMAT + reset,
        logging.INFO: green + LOG_FORMAT + reset,
        logging.WARNING: yellow + LOG_FORMAT + reset,
        logging.ERROR: red + LOG_FORMAT + reset,
        logging.CRITICAL: bold_red + LOG_FORMAT + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

class Logger:
    
    @staticmethod
    def get_logger(logger_name="logging", level=logging.INFO, colour_format=True):
        global _warned_invalid_level
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        # Case insensitive, e.g. debug, DEBUG or Debug
        env_level = os.getenv('NITRA_LOGGING_LEVEL', '').strip().upper()
        invalid_level = env_level and not isinstance(logging.getLevelName(env_level), int)
        logger.setLevel(env_level if env_level and not invalid_level else level)
        ch = logging.StreamHandler()
        if colour_format:
            ch.setFormatter(LoggerFormatter())
        else:
            ch.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(ch)
        if invalid_level and not _warned_invalid_level:
            _warned_invalid_level = True
            logger.warning(f"Ignoring invalid NITRA_LOGGING_LEVEL '{os.getenv('NITRA_LOGGING_LEVEL')}', expected one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        return logger
