import sys

from nitra.lib.hooks.hook_base import HookBase


class Hook(HookBase):


    def __init__(self, logger, arguments, environment=None):
        super().__init__(logger, arguments, environment)

    def execute_hook(self):
        self._logger.info(f"Running Bash Hook: {self._arguments}")
        returncode = self._subproc.run_command_streamed(["sh", f"scripts/{self._arguments}"], self._environment)
        if returncode != 0:
            self._logger.error(f"Bash Hook failed with exit code {returncode}: {self._arguments}")
            sys.exit(1)
