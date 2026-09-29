import json
import sys

from nitra.lib.logger import Logger as logger

# The ARM template schema a compiled Bicep file declares, mapped to the Nitra scope that deploys it
TEMPLATE_SCOPES = {
    "deploymentTemplate.json": "resource_group",
    "subscriptionDeploymentTemplate.json": "subscription",
}


class Validator():
    # Checks configurations and templates without deploying anything or contacting Azure, so it can run
    # in CI on every pull request. Every problem is collected and reported together

    def __init__(self, orchestrator):
        self.logger = logger.get_logger()
        self.logger.propagate = False
        self.orchestrator = orchestrator
        self.subproc = orchestrator.subproc
        self.problems = {}
        self.templates = {}
        self.checked_locations = set()

    def add_problem(self, path, problem):
        self.problems.setdefault(path, []).append(problem)

    def validate(self, path=None):
        configurations = self.orchestrator.resolve_scope(path)
        for configuration in configurations:
            self.validate_configuration(configuration)
        self.find_circular_references(configurations)
        return self.report(configurations)

    def validate_configuration(self, configuration):
        path = f"configuration/{configuration}"
        if len(configuration.split("/")) != 3:
            self.add_problem(path, "configurations must be at configuration/<subscription>/<resource_group>/<name>.yaml")
            return
        self.validate_location(configuration)
        config, error = self.orchestrator.read_yaml(path)
        if error:
            self.add_problem(path, error)
            return
        errors = self.orchestrator.validate_config(path, config, "deploy")
        for error in errors:
            self.add_problem(path, error)
        # The template checks need a config that is otherwise valid
        if not errors:
            self.validate_against_template(path, config)

    def validate_location(self, configuration):
        subscription, resource_group, _ = configuration.split("/")
        location_path = f"configuration/{subscription}/{resource_group}/location.yaml"
        if location_path in self.checked_locations:
            return
        self.checked_locations.add(location_path)
        location, error = self.orchestrator.read_yaml(location_path)
        if error == "file not found":
            self.add_problem(location_path, "file not found, every resource group folder needs one, e.g. 'location: australiaeast'")
        elif error:
            self.add_problem(location_path, error)
        elif not isinstance(location, dict) or not isinstance(location.get("location"), str) or not location["location"]:
            self.add_problem(location_path, "'location' is required, e.g. 'location: australiaeast'")

    def get_template(self, bicep):
        # Each template is compiled once, locally, and a compile error is reported once against the template
        if bicep not in self.templates:
            returncode, output = self.subproc.build_bicep(bicep)
            template = None
            if returncode != 0:
                errors = [line.strip() for line in output.splitlines() if "error" in line.lower()]
                self.add_problem(f"bicep/{bicep}", "does not compile: " + ("\n    ".join(errors) or output.strip()))
            else:
                try:
                    template = json.loads(output)
                except ValueError:
                    self.add_problem(f"bicep/{bicep}", "compiled output could not be read")
            self.templates[bicep] = template
        return self.templates[bicep]

    def validate_against_template(self, path, config):
        bicep = config["bicep_path"]
        template = self.get_template(bicep)
        if template is None:
            return

        schema = template.get("$schema", "")
        template_scope = next((scope for name, scope in TEMPLATE_SCOPES.items() if schema.endswith(f"/{name}#") or schema.endswith(f"/{name}")), None)
        config_scope = config.get("scope", "resource_group")
        if template_scope is None:
            self.add_problem(path, f"bicep/{bicep} targets a scope Nitra does not deploy to ({schema})")
        elif template_scope == "subscription" and config_scope != "subscription":
            self.add_problem(path, f"bicep/{bicep} has targetScope = 'subscription', set 'scope: subscription'")
        elif template_scope == "resource_group" and config_scope != "resource_group":
            self.add_problem(path, f"bicep/{bicep} deploys to a resource group, remove 'scope: {config_scope}'")

        # ARM matches parameter names without regard to case
        declared = {name.lower(): (name, parameter) for name, parameter in template.get("parameters", {}).items()}
        params = {name.lower() for name in (config.get("params") or {})}
        for name in config.get("params") or {}:
            if name.lower() not in declared:
                self.add_problem(path, f"param '{name}' is not a parameter of bicep/{bicep}")
        for key, (name, parameter) in declared.items():
            if key not in params and "defaultValue" not in parameter and not parameter.get("nullable"):
                self.add_problem(path, f"param '{name}' is required by bicep/{bicep} but not set")

    def find_circular_references(self, configurations):
        reported = set()
        finished = set()

        def visit(configuration, chain):
            if configuration in chain:
                cycle = chain[chain.index(configuration):] + (configuration,)
                if frozenset(cycle) not in reported:
                    reported.add(frozenset(cycle))
                    self.add_problem(f"configuration/{cycle[0]}", "circular reference: " + " -> ".join(cycle))
                return
            if configuration in finished:
                return
            for reference in self.orchestrator.get_references(configuration):
                visit(reference, chain + (configuration,))
            finished.add(configuration)

        for configuration in configurations:
            visit(configuration, ())

    def report(self, configurations):
        problem_count = sum(len(problems) for problems in self.problems.values())
        if problem_count == 0:
            templates = len([t for t in self.templates.values() if t is not None])
            self.logger.info(f"Validation passed: {len(configurations)} configuration(s), {templates} template(s)")
            return True
        lines = []
        for path, problems in self.problems.items():
            lines.append(path)
            lines += [f"  - {problem}" for problem in problems]
        self.logger.error("Validation failed:\n" + "\n".join(lines) + f"\n{problem_count} problem(s) in {len(self.problems)} file(s)")
        sys.exit(1)
