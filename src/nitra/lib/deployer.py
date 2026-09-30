import json
import logging
import os
import sys
import tempfile
import yaml

from nitra.lib.logger import Logger as logger
from nitra.lib.reference import is_reference, parse_reference

# How Bicep @secure() parameters appear in the compiled ARM template
SECURE_PARAMETER_TYPES = ("securestring", "secureobject")

class Deployer():

    def __init__(self, subproc, subscription):
        self.logger = logger.get_logger()
        self.logger.propagate = False
        self.subscription = subscription
        self.subproc = subproc

    def resource_group_exists(self, resource_group, subscription_id):
        returncode, output = self.subproc.resource_group_exists(resource_group, subscription_id)
        if returncode != 0:
            self.logger.error(f"Unable to check if resource group exists: {resource_group}\n{output.strip()}")
            sys.exit(1)
        return output.strip() == "true"

    def create_resource_group(self, resource_group, location, subscription_id):
        returncode, output = self.subproc.create_resource_group(resource_group, location, subscription_id)
        if returncode != 0:
            self.logger.error(f"Failed to create resource group: {resource_group}\n{output.strip()}")
            sys.exit(1)

    def get_reference_scope(self, configuration):
        with open(f"configuration/{configuration}") as file:
            config = yaml.safe_load(file)
        return config.get("scope", "resource_group")

    def get_deployment_output(self, deployment_name, output_name, resource_group, subscription, scope="resource_group"):
        self.logger.debug(f"Getting Deployment Output: {deployment_name}:{output_name}")
        if scope == "subscription":
            resource_group = None
        subscription_id = self.subscription.get_subscription_id(subscription)
        returncode, result = self.subproc.get_stack(deployment_name, resource_group, subscription_id)
        if returncode != 0:
            self.logger.error(f"DEPLOYMENT NOT FOUND: {deployment_name}")
            sys.exit(1)
        outputs = json.loads(result).get("outputs") or {}
        if output_name not in outputs:
            self.logger.error(f"Deployment output not found: {deployment_name}:{output_name}")
            sys.exit(1)
        return(outputs[output_name]["value"])

    def get_deployment_output_param(self, value):
        reference = parse_reference(value)
        scope = self.get_reference_scope(reference.configuration)
        return self.get_deployment_output(reference.deployment_name, reference.output, reference.resource_group, reference.subscription, scope)

    def build_parameters(self, params):
        parameters = {}
        for param, value in params.items():
            if is_reference(value):
                value = self.get_deployment_output_param(value)
            parameters[param] = {"value": value}
        return parameters

    def get_secure_parameters(self, bicep):
        # Names of the parameters the template marks @secure(), or None if the template cannot be compiled
        returncode, output = self.subproc.build_bicep(bicep)
        if returncode != 0:
            return None
        try:
            template = json.loads(output)
        except ValueError:
            return None
        return {name for name, parameter in template.get("parameters", {}).items()
                if str(parameter.get("type", "")).lower() in SECURE_PARAMETER_TYPES}

    def log_parameters(self, parameters, bicep):
        # The template is only compiled when debug logging is on. Values of @secure() parameters are never
        # logged, and if the template cannot be read no values are logged at all
        if not self.logger.isEnabledFor(logging.DEBUG):
            return
        secure = self.get_secure_parameters(bicep)
        if secure is None:
            self.logger.debug(f"Deployment Parameters (values hidden, unable to read which parameters are @secure() in bicep/{bicep}): {', '.join(parameters)}")
            return
        shown = {name: ({"value": "***"} if name in secure else value) for name, value in parameters.items()}
        self.logger.debug(f"Deployment Parameters: {json.dumps(shown, default=str)}")

    def write_parameters_file(self, params, bicep):
        return self.write_parameters(self.build_parameters(params), bicep)

    def write_parameters(self, parameters, bicep):
        # parameters are already resolved, e.g. {"name": {"value": "x"}}
        parameters_file = {
            "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
            "contentVersion": "1.0.0.0",
            "parameters": parameters
        }
        self.log_parameters(parameters_file["parameters"], bicep)
        with tempfile.NamedTemporaryFile(mode="w", prefix="nitra-", suffix=".json", delete=False) as file:
            json.dump(parameters_file, file, default=str)
        return file.name

    def deploy_bicep(self, params, bicep, resource_group, location, deployment_name, action_on_unmanage, deny_settings_mode, subscription):
        subscription_id = self.subscription.get_subscription_id(subscription)
        if not self.resource_group_exists(resource_group, subscription_id):
            self.create_resource_group(resource_group, location, subscription_id)

        self.logger.debug(f"Deployment Name: {deployment_name}")
        self.logger.debug(f"Deployment Subscription: {subscription}")
        self.logger.debug(f"Deployment Resource Group: {resource_group}")
        parameters_file = self.write_parameters_file(params, bicep)
        try:
            returncode, deploy_result = self.subproc.deploy_group_create(bicep, resource_group, deployment_name, action_on_unmanage, deny_settings_mode, parameters_file, subscription_id)
        finally:
            os.remove(parameters_file)
        if returncode == 0:
            self.logger.info("Deploy Complete\n")
            return
        self.logger.error(f"DEPLOYMENT FAILED: {deploy_result}")
        sys.exit(1)

    def deploy_bicep_subscription(self, params, bicep, location, deployment_name, action_on_unmanage, deny_settings_mode, subscription):
        subscription_id = self.subscription.get_subscription_id(subscription)

        self.logger.debug(f"Deployment Name: {deployment_name}")
        self.logger.debug(f"Deployment Subscription: {subscription}")
        parameters_file = self.write_parameters_file(params, bicep)
        try:
            returncode, deploy_result = self.subproc.deploy_subscription_create(bicep, deployment_name, action_on_unmanage, deny_settings_mode, parameters_file, location, subscription_id)
        finally:
            os.remove(parameters_file)
        if returncode == 0:
            self.logger.info("Deploy Complete\n")
            return
        self.logger.error(f"DEPLOYMENT FAILED: {deploy_result}")
        sys.exit(1)

    def destroy_bicep(self, resource_group, deployment_name, subscription, action_on_unmanage):
        subscription_id = self.subscription.get_subscription_id(subscription)
        if not self.resource_group_exists(resource_group, subscription_id):
            self.logger.error(f"Destroy Failed: Resource Group Not Found: {resource_group}")
            sys.exit(1)

        self.logger.debug(f"Destroy: Deployment Name: {deployment_name}")
        self.logger.debug(f"Destroy: Deployment Subscription: {subscription}")
        self.logger.debug(f"Destroy: Deployment Resource Group: {resource_group}")

        # Now we can destroy the deployment group
        returncode, destroy_result = self.subproc.deploy_group_destroy(resource_group, deployment_name, action_on_unmanage, subscription_id)
        if returncode == 0:
            self.logger.info("Destroy Complete\n")
            return
        self.logger.error(f"DESTROY FAILED: {destroy_result}")
        sys.exit(1)

    def destroy_bicep_subscription(self, deployment_name, subscription, action_on_unmanage):
        subscription_id = self.subscription.get_subscription_id(subscription)

        self.logger.debug(f"Destroy:  Name: {deployment_name}")
        self.logger.debug(f"Destroy:  Subscription: {subscription}")
        returncode, destroy_result = self.subproc.deploy_subscription_destroy(deployment_name, action_on_unmanage, subscription_id)
        if returncode == 0:
            self.logger.info("Destroy Complete\n")
            return
        self.logger.error(f"DESTROY FAILED: {destroy_result}")
        sys.exit(1)
