import sys

from nitra.lib.hooks.hook_base import HookBase


class Hook(HookBase):


    def __init__(self, logger, arguments, environment=None):
        super().__init__(logger, arguments, environment)

    def execute_hook(self):
        self._logger.info(f"Running Python3 Hook: {self._arguments}")
        returncode = self._subproc.run_command_streamed(["python3", f"scripts/{self._arguments}"], self._environment)
        if returncode != 0:
            self._logger.error(f"Python3 Hook failed with exit code {returncode}: {self._arguments}")
            sys.exit(1)
