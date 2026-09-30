import logging
import pytest

from nitra.lib import logger as logger_module
from nitra.lib.logger import Logger


@pytest.mark.parametrize("env_level, expected", [
    ("debug", logging.DEBUG),
    ("Warning", logging.WARNING),
    (" ERROR ", logging.ERROR),
    ("", logging.INFO),
])
def test_logging_level_from_environment(monkeypatch, env_level, expected):
    monkeypatch.setenv("NITRA_LOGGING_LEVEL", env_level)
    assert Logger.get_logger("test_logger_level").level == expected

def test_invalid_logging_level_warns_once(monkeypatch, capfd):
    monkeypatch.setenv("NITRA_LOGGING_LEVEL", "verbose")
    monkeypatch.setattr(logger_module, "_warned_invalid_level", False)
    assert Logger.get_logger("test_logger_invalid").level == logging.INFO
    Logger.get_logger("test_logger_invalid")
    assert capfd.readouterr().err.count("Ignoring invalid NITRA_LOGGING_LEVEL 'verbose'") == 1
