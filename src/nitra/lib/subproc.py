import os
import shlex
import subprocess
import sys

from nitra.lib.logger import Logger as logger

class Subproc():

    def __init__(self):
        self.logger = logger.get_logger()
        self.logger.propagate = False

    def _run(self, command, capture=True, environment=None):
        # Commands are argument lists, each item reaches the process as one argument so values can contain spaces
        self.logger.debug(f"command: {shlex.join(command)}")
        env = {**os.environ, **environment} if environment else None
        try:
            return subprocess.run(command, capture_output=capture, text=True, check=False, env=env)
        except FileNotFoundError:
            if command[0] == "az":
                self.logger.error("Azure CLI (az) not found on PATH, is it installed? https://learn.microsoft.com/cli/azure/install-azure-cli")
            else:
                self.logger.error(f"'{command[0]}' not found on PATH")
            sys.exit(1)

    def run_command(self, command):
        # stdout only, az writes warnings to stderr which would break json parsing
        return self._run(command).stdout

    def run_command_streamed(self, command, environment=None):
        # Output is not captured, it goes straight to the terminal / CI log as the command runs
        return self._run(command, capture=False, environment=environment).returncode

    def run_command_with_exit_code(self, command):
        result = self._run(command)
        return result.returncode, result.stdout + result.stderr

    def run_command_output_or_error(self, command):
        # stdout on success so it can be parsed, or az's error message on failure (e.g. not logged in)
        result = self._run(command)
        if result.returncode != 0:
            return result.returncode, result.stderr
        return result.returncode, result.stdout

    # Every command that acts on a subscription is given it with --subscription, so the user's
    # default subscription ('az account set') is never changed and parallel runs cannot interfere

    def resource_group_exists(self, resource_group, subscription_id):
        # stdout is "true" or "false"
        return self.run_command_output_or_error(["az", "group", "exists", "--name", resource_group, "--subscription", subscription_id])

    def create_resource_group(self, resource_group, location, subscription_id):
        self.logger.info(f"Creating resource group: '{resource_group}' in {location}")
        return self.run_command_with_exit_code(["az", "group", "create", "--location", location, "--name", resource_group, "--subscription", subscription_id, "--output", "json"])

    def deploy_group_create(self, bicep, resource_group, deployment_name, action_on_unmanage, deny_settings_mode, parameters_file, subscription_id):
        return self.run_command_with_exit_code([
            "az", "stack", "group", "create",
            "-f", f"bicep/{bicep}",
            "-g", resource_group,
            "--name", deployment_name,
            "--action-on-unmanage", action_on_unmanage,
            "--deny-settings-mode", deny_settings_mode,
            "--parameters", f"@{parameters_file}",
            "--subscription", subscription_id,
            "--yes", "--output", "json"
        ])

    def deploy_group_destroy(self, resource_group, deployment_name, action_on_unmanage, subscription_id):
        return self.run_command_with_exit_code([
            "az", "stack", "group", "delete",
            "-g", resource_group,
            "--name", deployment_name,
            "--action-on-unmanage", action_on_unmanage,
            "--subscription", subscription_id,
            "--yes", "--verbose", "--output", "json"
        ])

    def deploy_subscription_create(self, bicep, deployment_name, action_on_unmanage, deny_settings_mode, parameters_file, location, subscription_id):
        return self.run_command_with_exit_code([
            "az", "stack", "sub", "create",
            "-f", f"bicep/{bicep}",
            "--name", deployment_name,
            "--action-on-unmanage", action_on_unmanage,
            "--deny-settings-mode", deny_settings_mode,
            "--parameters", f"@{parameters_file}",
            "--location", location,
            "--subscription", subscription_id,
            "--yes", "--output", "json"
        ])

    def deploy_subscription_destroy(self, deployment_name, action_on_unmanage, subscription_id):
        return self.run_command_with_exit_code([
            "az", "stack", "sub", "delete",
            "--name", deployment_name,
            "--action-on-unmanage", action_on_unmanage,
            "--subscription", subscription_id,
            "--yes", "--output", "json"
        ])

    def get_stack(self, deployment_name, resource_group, subscription_id):
        # Subscription scoped stacks have no resource group
        if resource_group is None:
            command = ["az", "stack", "sub", "show", "--name", deployment_name, "--subscription", subscription_id, "--output", "json"]
        else:
            command = ["az", "stack", "group", "show", "--name", deployment_name, "--resource-group", resource_group, "--subscription", subscription_id, "--output", "json"]
        # stdout only, az writes warnings to stderr which would break json parsing
        result = self._run(command)
        return result.returncode, result.stdout

    def what_if_group(self, bicep, resource_group, parameters_file, subscription_id):
        # Azure calculates what the deployment would change, nothing is changed
        return self.run_command_output_or_error([
            "az", "deployment", "group", "what-if",
            "--resource-group", resource_group,
            "--template-file", f"bicep/{bicep}",
            "--parameters", f"@{parameters_file}",
            "--subscription", subscription_id,
            "--no-pretty-print", "--output", "json"
        ])

    def what_if_subscription(self, bicep, location, parameters_file, subscription_id):
        return self.run_command_output_or_error([
            "az", "deployment", "sub", "what-if",
            "--location", location,
            "--template-file", f"bicep/{bicep}",
            "--parameters", f"@{parameters_file}",
            "--subscription", subscription_id,
            "--no-pretty-print", "--output", "json"
        ])

    def build_bicep(self, bicep):
        # Compiles the template to ARM JSON locally, it does not contact Azure
        return self.run_command_output_or_error(["az", "bicep", "build", "--file", f"bicep/{bicep}", "--stdout"])

    def list_subscriptions(self):
        # Reads the subscriptions from the local az login, it does not change anything
        return self.run_command_output_or_error(["az", "account", "list", "--output", "json"])

    def check_azure_login(self):
        # Requesting a token authenticates against Azure, unlike 'az account show' which only reads the local cache.
        # Only the expiry is returned so the token itself never reaches the logs
        return self.run_command_output_or_error(["az", "account", "get-access-token", "--query", "expiresOn", "--output", "tsv"])
