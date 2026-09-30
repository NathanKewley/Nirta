import importlib

from nitra.lib.logger import Logger as logger

class HookOrchestrator():

    def __init__(self):
        self.logger = logger.get_logger()
        self.logger.propagate = False
    
    def run_hooks(self, hooks, environment=None):
        for hook_name, arguments in hooks.items():
            self.logger.debug(f"Hook: {hook_name}, Arguments: {arguments}")
            hook_module = importlib.import_module(f"nitra.lib.hooks.{hook_name}")
            hook = hook_module.Hook(self.logger, arguments, environment)
            hook.execute_hook()
