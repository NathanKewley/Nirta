import json
import os
import sys

from nitra.lib.logger import Logger as logger
from nitra.lib.reference import is_reference, parse_reference

# How each what-if change type is shown, and what it counts towards in the summary
CHANGE_TYPES = {
    "Create": ("+", "create"),
    "Modify": ("~", "modify"),
    "Delete": ("-", "delete"),
    "Deploy": ("!", "deploy, changes cannot be predicted"),
    "Unsupported": ("?", "cannot be previewed"),
    "NoChange": ("=", "unchanged"),
}
LONG_VALUE = 80


class Planner():
    # Previews what a deploy would change without changing anything. Azure what-if shows what each template
    # would create or modify, and the stack's current resources show what it would delete or detach because
    # they are no longer in the template, which what-if alone does not report. Hooks are not run

    def __init__(self, orchestrator):
        self.logger = logger.get_logger()
        self.logger.propagate = False
        self.orchestrator = orchestrator
        self.subproc = orchestrator.subproc
        self.deployer = orchestrator.deployer
        self.subscription = orchestrator.subscription
        self.totals = {}
        self.not_previewed = []
        self.failed = []

    def count(self, kind, amount=1):
        self.totals[kind] = self.totals.get(kind, 0) + amount

    def plan(self, path=None):
        targets = self.orchestrator.resolve_scope(path)
        # Load (and so validate) every config and check for cycles first, as a deploy would
        for configuration in targets:
            self.orchestrator.check_circular_references(configuration)
        configurations = self.with_dependencies(targets)
        for configuration in list(reversed(self.orchestrator.get_destroy_order(configurations))):
            self.plan_configuration(configuration, configuration in targets)
        return self.report()

    def with_dependencies(self, targets):
        # A deploy also redeploys the configs these take outputs from, so they are planned too
        configurations = list(targets)
        pending = list(targets)
        while pending:
            for reference in self.orchestrator.get_references(pending.pop()):
                if reference not in configurations:
                    configurations.append(reference)
                    pending.append(reference)
        return configurations

    def plan_configuration(self, configuration, is_target):
        config = self.orchestrator.load_config(configuration)
        location = self.orchestrator.load_location(configuration)
        deployment_name = self.orchestrator.get_deployment_name(configuration)
        subscription = self.orchestrator.get_subscription(configuration)
        resource_group = self.orchestrator.get_resource_group(configuration)
        scope = config.get("scope", "resource_group")
        subscription_id = self.subscription.get_subscription_id(subscription)
        header = f"{configuration} (stack {deployment_name})"

        if not is_target and self.orchestrator.can_skip_dependency(configuration, config, deployment_name, resource_group, subscription, scope):
            self.logger.info(f"{header}\n  not redeployed, it is already deployed and redeploy_as_dependency is false\n")
            return

        parameters, unknown = self.resolve_parameters(config["params"])
        if unknown:
            self.not_previewed.append(configuration)
            self.logger.warning(f"{header}\n  cannot be previewed until its dependencies are deployed:\n" + "\n".join(f"    {u}" for u in unknown) + "\n")
            return
        if scope == "resource_group" and not self.deployer.resource_group_exists(resource_group, subscription_id):
            self.not_previewed.append(configuration)
            self.logger.warning(f"{header}\n  resource group {resource_group} does not exist yet, it would be created in {location}."
                                f"\n  What-if needs the resource group to exist, so the resources in the template cannot be previewed\n")
            return

        parameters_file = self.deployer.write_parameters(parameters, config["bicep_path"])
        try:
            if scope == "subscription":
                returncode, output = self.subproc.what_if_subscription(config["bicep_path"], location, parameters_file, subscription_id)
            else:
                returncode, output = self.subproc.what_if_group(config["bicep_path"], resource_group, parameters_file, subscription_id)
        finally:
            os.remove(parameters_file)
        result = json.loads(output) if returncode == 0 else None
        if result is None or result.get("error"):
            self.failed.append(configuration)
            error = output.strip() if result is None else json.dumps(result["error"], indent=2)
            self.logger.error(f"{header}\n  what-if failed:\n{error}\n")
            return

        stack_resources = self.get_stack_resources(deployment_name, None if scope == "subscription" else resource_group, subscription_id)
        self.logger.info(self.describe(header, result.get("changes") or [], stack_resources, config) + "\n")

    def resolve_parameters(self, params):
        # Ref: values are read from the referenced stacks as they are now
        parameters = {}
        unknown = []
        for name, value in params.items():
            if is_reference(value):
                value, problem = self.read_output(parse_reference(value))
                if problem:
                    unknown.append(problem)
                    continue
            parameters[name] = {"value": value}
        return parameters, unknown

    def read_output(self, reference):
        scope = self.deployer.get_reference_scope(reference.configuration)
        resource_group = None if scope == "subscription" else reference.resource_group
        subscription_id = self.subscription.get_subscription_id(reference.subscription)
        returncode, output = self.subproc.get_stack(reference.deployment_name, resource_group, subscription_id)
        if returncode != 0:
            return None, f"{reference.configuration} is not deployed yet, so its output '{reference.output}' is not known"
        outputs = json.loads(output).get("outputs") or {}
        if reference.output not in outputs:
            return None, f"{reference.configuration} has no output '{reference.output}' yet, it may be added when that config is deployed"
        return outputs[reference.output]["value"], None

    def get_stack_resources(self, deployment_name, resource_group, subscription_id):
        # Resources the stack manages now, keyed by lower case id as resource ids are case insensitive
        returncode, output = self.subproc.get_stack(deployment_name, resource_group, subscription_id)
        if returncode != 0:
            return {}
        return {resource["id"].lower(): resource["id"] for resource in json.loads(output).get("resources", [])
                if resource.get("id") and resource.get("status", "managed") == "managed"}

    def describe(self, header, changes, stack_resources, config):
        lines = [header]
        # 'Ignore' means a resource exists alongside the template but is not in it, those are not managed by this stack
        in_template = {change["resourceId"].lower() for change in changes if change.get("changeType") != "Ignore"}
        unchanged = 0
        for change in changes:
            change_type = change.get("changeType")
            if change_type == "Ignore":
                continue
            if change_type == "NoChange":
                unchanged += 1
                continue
            symbol, kind = CHANGE_TYPES.get(change_type, ("?", change_type))
            self.count(kind)
            note = f" ({change['unsupportedReason']})" if change.get("unsupportedReason") else ""
            lines.append(f"  {symbol} {self.short_id(change['resourceId'])}{note}")
            lines += self.describe_delta(change.get("delta"), "      ")

        # Resources taken out of the template are deleted or detached by the stack on the next deploy
        action = config.get("action_on_unmanage", "deleteResources")
        for resource_key, resource_id in stack_resources.items():
            if resource_key not in in_template:
                if action == "detachAll":
                    self.count("detach")
                    lines.append(f"  - {self.short_id(resource_id)} (no longer in the template, the stack detaches it, it is kept)")
                else:
                    self.count("delete")
                    lines.append(f"  - {self.short_id(resource_id)} (no longer in the template, the stack deletes it)")

        if unchanged:
            self.count("unchanged", unchanged)
            lines.append(f"  = {unchanged} unchanged")
        if len(lines) == 1:
            lines.append("  no resources")
        return "\n".join(lines)

    def describe_delta(self, delta, indent):
        lines = []
        for change in delta or []:
            kind = change.get("propertyChangeType")
            path = change.get("path")
            if change.get("children"):
                lines.append(f"{indent}~ {path}:")
                lines += self.describe_delta(change["children"], indent + "    ")
            elif kind == "Modify":
                lines.append(f"{indent}~ {path}: {self.show(change.get('before'))} => {self.show(change.get('after'))}")
            elif kind == "Create":
                lines.append(f"{indent}+ {path}: {self.show(change.get('after'))}")
            elif kind == "Delete":
                lines.append(f"{indent}- {path}: {self.show(change.get('before'))}")
        return lines

    def show(self, value):
        text = json.dumps(value, default=str)
        return text if len(text) <= LONG_VALUE else text[:LONG_VALUE - 3] + "..."

    def short_id(self, resource_id):
        # The resource type and name are enough to recognise it, the subscription and resource group are in the header
        parts = resource_id.split("/providers/", 1)
        return parts[1] if len(parts) == 2 else resource_id.split("/", 3)[-1]

    def report(self):
        order = ["create", "modify", "delete", "detach", "deploy, changes cannot be predicted", "cannot be previewed", "unchanged"]
        summary = ", ".join(f"{self.totals[kind]} to {kind}" if kind in ("create", "modify", "delete", "detach") else f"{self.totals[kind]} {kind}"
                            for kind in order if self.totals.get(kind))
        self.logger.info(f"Plan: {summary or 'no changes'}")
        if self.not_previewed:
            self.logger.warning(f"{len(self.not_previewed)} configuration(s) could not be previewed, see above")
        if self.failed:
            self.logger.error(f"What-if failed for {len(self.failed)} configuration(s): {', '.join(self.failed)}")
            sys.exit(1)
        return True
