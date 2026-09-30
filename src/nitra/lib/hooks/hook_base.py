from nitra.lib.subproc import Subproc

class HookBase():

    def __init__(self, logger, hook_arguments, environment=None):
        self._logger = logger
        self._arguments = hook_arguments
        # Extra environment variables for the script, e.g. NITRA_SUBSCRIPTION_ID
        self._environment = environment or {}
        self._subproc = Subproc()

    def execute_hook(self):
        raise NotImplementedError()
