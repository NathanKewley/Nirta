import yaml
import json
import os
import pkgutil
import sys

from nitra.lib.subproc import Subproc
from nitra.lib.logger import Logger as logger
from nitra.lib.deployer import Deployer
from nitra.lib.subscription import Subscription
from nitra.lib.hook_orchestrator import HookOrchestrator
from nitra.lib import hooks
from nitra.lib.reference import InvalidReference, is_reference, parse_reference
from nitra.lib.validator import Validator

CONFIG_KEYS = {"bicep_path", "scope", "params", "action_on_unmanage", "deny_settings_mode", "pre_hooks", "post_hooks", "redeploy_as_dependency"}
SCOPES = ("resource_group", "subscription")
HOOK_TYPES = sorted(module.name for module in pkgutil.iter_modules(hooks.__path__) if module.name != "hook_base")

class Orchestrator():

    def __init__(self):
        self.logger = logger.get_logger()
        self.logger.propagate = False
        self.subproc = Subproc()
        self.subscription = Subscription(self.subproc)
        self.deployer = Deployer(self.subproc, self.subscription)
        self.hook_orchestrator = HookOrchestrator()
        self.deploys = []
        self.loaded_configs = {}
        self.reference_checked = set()
        # Configs explicitly asked for in this run, these are always deployed even when also reached as a dependency
        self.targets = set()
        # Set by --yes, skips the confirmation before destroying
        self.assume_yes = False

    def get_deployment_name(self, configuration):
        return configuration.replace("/",".")[:-5]

    def get_resource_group(self, configuration):
        return configuration.split('/')[1]

    def get_subscription(self, configuration):
        return configuration.split('/')[0]

    def get_child_directories(self, path):
        # Subscription and resource group folders, ignoring stray files and hidden folders
        return sorted(item for item in os.listdir(path) if os.path.isdir(os.path.join(path, item)) and not item.startswith("."))

    def get_configurations(self, path):
        configurations = []
        for item in sorted(os.listdir(path)):
            if item == "location.yaml" or item.startswith(".") or not os.path.isfile(os.path.join(path, item)):
                continue
            if item.endswith(".yaml"):
                configurations.append(item)
            elif item.endswith(".yml"):
                self.logger.warning(f"Skipping {os.path.join(path, item)}: configuration files must use the .yaml extension")
            else:
                self.logger.debug(f"Skipping non configuration file: {os.path.join(path, item)}")
        return configurations

    def config_error(self, path, errors):
        problems = "\n".join(f"  - {error}" for error in errors)
        self.logger.error(f"Invalid configuration: {path}\n{problems}")
        sys.exit(1)

    def read_yaml(self, path):
        # Returns (data, error) rather than exiting, so validate can report every broken file
        if not os.path.isfile(path):
            return None, "file not found"
        try:
            with open(path) as file:
                return yaml.safe_load(file), None
        except yaml.YAMLError as e:
            return None, f"invalid YAML: {e}"

    def load_yaml(self, path):
        data, error = self.read_yaml(path)
        if error:
            self.config_error(path, [error])
        return data

    def validate_config(self, path, config, deploy_mode):
        if not isinstance(config, dict):
            return ["must be a mapping of settings, e.g. 'bicep_path: storage/storage_account.bicep'"]
        errors = []

        for key in config:
            if key not in CONFIG_KEYS:
                self.logger.warning(f"Unknown setting '{key}' in {path} will be ignored, expected one of: {', '.join(sorted(CONFIG_KEYS))}")

        if config.get("scope", "resource_group") not in SCOPES:
            errors.append(f"'scope' must be 'resource_group' or 'subscription', got '{config['scope']}'")

        if not isinstance(config.get("redeploy_as_dependency", True), bool):
            errors.append("'redeploy_as_dependency' must be true or false")

        if config.get("params") is not None and not isinstance(config["params"], dict):
            errors.append("'params' must be a mapping of parameter names to values")
        elif deploy_mode == "deploy":
            for param, value in (config.get("params") or {}).items():
                if is_reference(value):
                    try:
                        parse_reference(value)
                    except InvalidReference as e:
                        errors.append(f"param '{param}': {e}")

        # Destroy only needs the stack name and scope, so a config whose template has been removed can still be destroyed
        if deploy_mode == "deploy":
            bicep_path = config.get("bicep_path")
            if not isinstance(bicep_path, str) or not bicep_path:
                errors.append("'bicep_path' is required, e.g. 'bicep_path: storage/storage_account.bicep'")
            elif not os.path.isfile(os.path.join("bicep", bicep_path)):
                errors.append(f"'bicep_path' file not found: bicep/{bicep_path}")

        for hook_section in ("pre_hooks", "post_hooks"):
            hook_config = config.get(hook_section)
            if hook_config is None:
                continue
            if not isinstance(hook_config, dict):
                errors.append(f"'{hook_section}' must be a mapping of hook type to script, e.g. 'BashScript: my_script.sh'")
                continue
            for hook_type, script in hook_config.items():
                if hook_type not in HOOK_TYPES:
                    errors.append(f"unknown hook type '{hook_type}' in '{hook_section}', expected one of: {', '.join(HOOK_TYPES)}")
                elif deploy_mode == "deploy" and not os.path.isfile(os.path.join("scripts", str(script))):
                    errors.append(f"'{hook_section}' script not found: scripts/{script}")

        return errors

    def load_config(self, config, deploy_mode="deploy"):
        # Cached so configs visited by the circular reference check are not read and validated twice
        if (config, deploy_mode) in self.loaded_configs:
            return self.loaded_configs[(config, deploy_mode)]
        path = f"configuration/{config}"
        loaded = self.load_yaml(path)
        errors = self.validate_config(path, loaded, deploy_mode)
        if errors:
            self.config_error(path, errors)
        if loaded.get("params") is None:
            loaded["params"] = {}
        self.loaded_configs[(config, deploy_mode)] = loaded
        return loaded

    def check_circular_references(self, configuration, chain=()):
        # Walks the Ref: dependencies before anything is deployed, so a cycle fails without running hooks or deployments
        if configuration in chain:
            cycle = chain[chain.index(configuration):] + (configuration,)
            self.logger.error("Circular reference between configurations:\n  " + "\n  -> ".join(cycle))
            sys.exit(1)
        if configuration in self.reference_checked:
            return
        config = self.load_config(configuration)
        for value in config["params"].values():
            if is_reference(value):
                self.check_circular_references(parse_reference(value).configuration, chain + (configuration,))
        self.reference_checked.add(configuration)

    def load_location(self, config):
        location_path = "configuration/" + config.split("/")[0] + "/" + config.split("/")[1] + "/location.yaml"
        if not os.path.isfile(location_path):
            self.config_error(location_path, ["file not found, every resource group folder needs one, e.g. 'location: australiaeast'"])
        location = self.load_yaml(location_path)
        if not isinstance(location, dict) or not isinstance(location.get("location"), str) or not location["location"]:
            self.config_error(location_path, ["'location' is required, e.g. 'location: australiaeast'"])
        return location["location"]

    def stack_exists(self, deployment_name, resource_group, subscription, scope):
        if scope == "subscription":
            resource_group = None
        returncode, _ = self.subproc.get_stack(deployment_name, resource_group, self.subscription.get_subscription_id(subscription))
        return returncode == 0

    def stack_deployed(self, deployment_name, resource_group, subscription, scope):
        # Only a stack that last deployed successfully is sure to have its outputs
        if scope == "subscription":
            resource_group = None
        returncode, output = self.subproc.get_stack(deployment_name, resource_group, self.subscription.get_subscription_id(subscription))
        if returncode != 0:
            return False
        return str(json.loads(output).get("provisioningState", "")).lower() == "succeeded"

    def check_deployment_dependancy(self, value):
        reference = parse_reference(value)
        # Dependencies are redeployed so their hooks run and outputs are current, unless the
        # dependency sets 'redeploy_as_dependency: false' (see deploy)
        self.logger.info("Deployment has dependencies. Resolving...")
        self.deploy(reference.configuration, as_dependency=True)

    def deploy(self, configuration, deploy_mode="deploy", dry_run=False, as_dependency=False):
        if configuration in self.deploys:
            return
        if deploy_mode == "deploy":
            self.check_circular_references(configuration)
        self.deploys.append(configuration)

        config = self.load_config(configuration, deploy_mode)
        location = self.load_location(configuration)
        deployment_name = self.get_deployment_name(configuration)
        subscription = self.get_subscription(configuration)
        resource_group = self.get_resource_group(configuration)
        scope = config.get("scope", "resource_group")
        if not dry_run:
            # Fails on an unknown subscription before any hooks or deployments run
            self.subscription.get_subscription_id(subscription)

        if deploy_mode == "deploy" and not dry_run:
            if as_dependency and self.can_skip_dependency(configuration, config, deployment_name, resource_group, subscription, scope):
                self.logger.info(f"Skipping {configuration}: already deployed and redeploy_as_dependency is false\n")
                return
            # Deploy the configs this one takes outputs from first, destroy does not need this ordering
            self.deploy_dependencies(config)

        self.logger.info(f"{deploy_mode}ing: {configuration} to {subscription}")
        if dry_run:
            return [config['params'], config['bicep_path'], resource_group, location, deployment_name, subscription]
        if deploy_mode == "deploy":
            self.deploy_stack(configuration, config, location, deployment_name, resource_group, subscription, scope)
        else:
            self.destroy_stack(config, deployment_name, resource_group, subscription, scope)

    def can_skip_dependency(self, configuration, config, deployment_name, resource_group, subscription, scope):
        # A config only reached through a Ref: can opt out of being redeployed when it is already deployed,
        # but never when it is part of what was asked to be deployed
        return (configuration not in self.targets
                and not config.get("redeploy_as_dependency", True)
                and self.stack_deployed(deployment_name, resource_group, subscription, scope))

    def deploy_dependencies(self, config):
        for value in config['params'].values():
            if is_reference(value):
                self.check_deployment_dependancy(value)

    def run_hooks(self, hooks, environment):
        # az commands run by the hook default to the config's subscription, see Subscription.az_config_for
        with self.subscription.az_config_for(environment["NITRA_SUBSCRIPTION_ID"]) as config_dir:
            if config_dir:
                # az starts a background telemetry upload after each command that writes into the config folder,
                # after the hook has finished and the folder is removed, so it is turned off for hook az commands
                environment = {**environment, "AZURE_CONFIG_DIR": config_dir, "AZURE_CORE_COLLECT_TELEMETRY": "false"}
            self.hook_orchestrator.run_hooks(hooks, environment)

    def hook_environment(self, configuration, deployment_name, resource_group, subscription, location, scope):
        # Hooks are told where the deployment is going, e.g. for tools other than az
        environment = {
            "NITRA_CONFIGURATION": configuration,
            "NITRA_DEPLOYMENT_NAME": deployment_name,
            "NITRA_SUBSCRIPTION": subscription,
            "NITRA_SUBSCRIPTION_ID": self.subscription.get_subscription_id(subscription),
            "NITRA_LOCATION": location,
        }
        if scope == "resource_group":
            environment["NITRA_RESOURCE_GROUP"] = resource_group
        return environment

    def deploy_stack(self, configuration, config, location, deployment_name, resource_group, subscription, scope):
        action_on_unmanage = config.get("action_on_unmanage", "deleteResources")
        deny_settings_mode = config.get("deny_settings_mode", "None")
        environment = self.hook_environment(configuration, deployment_name, resource_group, subscription, location, scope)
        if config.get('pre_hooks'):
            self.run_hooks(config['pre_hooks'], environment)
        if scope == "subscription":
            self.deployer.deploy_bicep_subscription(config['params'], config['bicep_path'], location, deployment_name, action_on_unmanage, deny_settings_mode, subscription)
        else:
            self.deployer.deploy_bicep(config['params'], config['bicep_path'], resource_group, location, deployment_name, action_on_unmanage, deny_settings_mode, subscription)
        if config.get('post_hooks'):
            self.run_hooks(config['post_hooks'], environment)

    def destroy_stack(self, config, deployment_name, resource_group, subscription, scope):
        action_on_unmanage = config.get("action_on_unmanage", "deleteResources")
        if not self.stack_exists(deployment_name, resource_group, subscription, scope):
            self.logger.info(f"Stack not found, skipping destroy: {deployment_name}\n")
        elif scope == "subscription":
            self.deployer.destroy_bicep_subscription(deployment_name, subscription, action_on_unmanage)
        else:
            self.deployer.destroy_bicep(resource_group, deployment_name, subscription, action_on_unmanage)

    def deploy_resource_group(self, configuration, deploy_mode="deploy", dry_run=False):
        test_results = []

        subscription = self.get_subscription(configuration)
        resource_group = self.get_resource_group(configuration)
        if deploy_mode == "deploy" and not dry_run:
            self.targets.update(self.collect_configurations(subscription, resource_group))
        deployments = self.get_configurations(f"configuration/{configuration}/")
        for deployment in deployments:
            if not dry_run:
                self.deploy(f"{subscription}/{resource_group}/{deployment}", deploy_mode=deploy_mode)
            else:
                test_results.append(f"{subscription}/{resource_group}/{deployment}")
        if dry_run:
            return test_results

    def deploy_subscription(self, configuration, deploy_mode="deploy", dry_run=False):
        test_results = []
        if deploy_mode == "deploy" and not dry_run:
            self.targets.update(self.collect_configurations(configuration))
        resource_groups = self.get_child_directories(f"configuration/{configuration}/")
        for resource_group in resource_groups:
            if not dry_run:
                self.deploy_resource_group(f"{configuration}/{resource_group}", deploy_mode=deploy_mode)
            else:
                test_results.append(f"{configuration}/{resource_group}")
        if dry_run:
            return test_results                

    def deploy_account(self, deploy_mode="deploy", dry_run=False):
        test_results = []
        if deploy_mode == "deploy" and not dry_run:
            self.targets.update(self.collect_configurations())
        subscriptions = self.get_child_directories("configuration/")
        for subscription in subscriptions:
            if not dry_run:
                self.deploy_subscription(subscription, deploy_mode=deploy_mode)
            else:
                test_results.append(subscription)
        if dry_run:
            return test_results                        

    def collect_configurations(self, subscription=None, resource_group=None):
        # Every config under the account, one subscription, or one resource group
        subscriptions = [subscription] if subscription else self.get_child_directories("configuration/")
        configurations = []
        for sub in subscriptions:
            resource_groups = [resource_group] if resource_group else self.get_child_directories(f"configuration/{sub}/")
            for rg in resource_groups:
                configurations += [f"{sub}/{rg}/{config}" for config in self.get_configurations(f"configuration/{sub}/{rg}/")]
        return configurations

    def get_references(self, configuration):
        # Configs this one takes outputs from. Lenient on purpose: a config that no longer loads,
        # or a reference to a config that was removed, should not stop a destroy
        try:
            with open(f"configuration/{configuration}") as file:
                params = (yaml.safe_load(file) or {}).get("params") or {}
        except Exception:
            return []
        references = []
        for value in (params.values() if isinstance(params, dict) else []):
            if is_reference(value):
                try:
                    references.append(parse_reference(value).configuration)
                except InvalidReference:
                    pass
        return references

    def get_destroy_order(self, configurations):
        # Reverse of deploy order, so a config is destroyed before the configs it takes outputs from
        selected = set(configurations)
        deploy_order = []
        visited = set()

        def visit(configuration):
            # Marked on entry so a circular reference cannot loop forever
            if configuration in visited:
                return
            visited.add(configuration)
            for reference in sorted(self.get_references(configuration)):
                if reference in selected:
                    visit(reference)
            deploy_order.append(configuration)

        for configuration in sorted(configurations):
            visit(configuration)
        return list(reversed(deploy_order))

    def warn_about_remaining_dependents(self, configurations):
        selected = set(configurations)
        for configuration in self.collect_configurations():
            if configuration in selected:
                continue
            for reference in self.get_references(configuration):
                if reference in selected:
                    self.logger.warning(f"{configuration} takes outputs from {reference}, which is being destroyed. {configuration} is not part of this destroy")

    def destroy_configurations(self, configurations):
        # Check every config before destroying anything, so a bad file cannot stop a destroy part way through
        for configuration in configurations:
            self.load_config(configuration, deploy_mode="destroy")
        if not configurations:
            self.logger.info("Nothing to destroy")
            return
        self.warn_about_remaining_dependents(configurations)
        destroy_order = self.get_destroy_order(configurations)
        self.confirm_destroy(destroy_order)
        for configuration in destroy_order:
            self.deploy(configuration, deploy_mode="destroy")

    def confirm_destroy(self, destroy_order):
        effects = {
            "deleteResources": "deletes its resources",
            "deleteAll": "deletes its resources and resource groups",
            "detachAll": "detaches its resources, they are kept",
        }
        lines = []
        for number, configuration in enumerate(destroy_order, 1):
            action = self.load_config(configuration, deploy_mode="destroy").get("action_on_unmanage", "deleteResources")
            lines.append(f"  {number}. {configuration} ({effects.get(action, action)})")
        self.logger.warning(f"Destroying {len(destroy_order)} stack(s), in this order:\n" + "\n".join(lines))
        if self.assume_yes:
            return
        # CI and other non interactive runs must opt in explicitly, they are never left waiting for input
        if not sys.stdin.isatty():
            self.logger.error("Refusing to destroy without confirmation as there is no terminal to ask on, pass --yes to confirm")
            sys.exit(1)
        if input("Type 'yes' to destroy these stacks: ").strip() != "yes":
            self.logger.error("Destroy cancelled, nothing was destroyed")
            sys.exit(1)

    def destroy(self, configuration):
        self.destroy_configurations([configuration])

    def destroy_resource_group(self, configuration):
        self.destroy_configurations(self.collect_configurations(self.get_subscription(configuration), self.get_resource_group(configuration)))

    def destroy_subscription(self, configuration):
        self.destroy_configurations(self.collect_configurations(configuration))

    def destroy_account(self):
        self.destroy_configurations(self.collect_configurations())

    def validate(self, path=None):
        return Validator(self).validate(path)
