import os
from collections import namedtuple

REF_PREFIX = "Ref:"
REF_FORMAT = "Ref:<subscription>/<resource_group>/<config>:<output>"


class InvalidReference(ValueError):
    pass


class Reference(namedtuple("Reference", ["subscription", "resource_group", "config", "output"])):

    @property
    def configuration(self):
        # Path of the referenced config relative to the configuration folder
        return f"{self.subscription}/{self.resource_group}/{self.config}.yaml"

    @property
    def deployment_name(self):
        # Matches the stack name the orchestrator gives the referenced config
        return f"{self.subscription}.{self.resource_group}.{self.config}"


def is_reference(value):
    return isinstance(value, str) and value.startswith(REF_PREFIX)


def parse_reference(value, configuration_root="configuration"):
    body = value[len(REF_PREFIX):]
    if ":" not in body:
        raise InvalidReference(f"'{value}' is missing the output name, expected {REF_FORMAT}")
    path, output = body.rsplit(":", 1)
    if not path or not output:
        raise InvalidReference(f"'{value}' is not a valid reference, expected {REF_FORMAT}")

    if "/" in path:
        parts = path.split("/")
        if len(parts) != 3 or not all(parts):
            raise InvalidReference(f"'{value}' is not a valid reference, expected {REF_FORMAT}")
        candidates = [tuple(parts)]
    else:
        # Dotted form: names can contain dots, so try every split into subscription.resource_group.config
        pieces = path.split(".")
        candidates = [
            (".".join(pieces[:i]), ".".join(pieces[i:j]), ".".join(pieces[j:]))
            for i in range(1, len(pieces) - 1)
            for j in range(i + 1, len(pieces))
        ]
        if not candidates:
            raise InvalidReference(f"'{value}' is not a valid reference, expected {REF_FORMAT}")

    matches = [c for c in candidates if os.path.isfile(os.path.join(configuration_root, *c) + ".yaml")]
    if not matches:
        if len(candidates) == 1:
            missing = os.path.join(configuration_root, *candidates[0]) + ".yaml"
            raise InvalidReference(f"'{value}' refers to a configuration that does not exist: {missing}")
        raise InvalidReference(f"'{value}' does not match any configuration file")
    if len(matches) > 1:
        options = ", ".join(f"{REF_PREFIX}{'/'.join(m)}:{output}" for m in matches)
        raise InvalidReference(f"'{value}' is ambiguous, use one of: {options}")
    return Reference(*matches[0], output)
