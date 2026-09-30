import pytest

from nitra.lib.reference import InvalidReference, Reference, is_reference, parse_reference


def make_config(root, *parts):
    path = root.joinpath("configuration", *parts[:-1])
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{parts[-1]}.yaml").write_text("---\n")

def test_is_reference():
    assert is_reference("Ref:sub.rg.config:output")
    assert not is_reference("storageRef:sub.rg.config:output")
    assert not is_reference("australiaeast")
    assert not is_reference(["Ref:sub.rg.config:output"])
    assert not is_reference(True)

def test_dotted_reference(tmp_path):
    make_config(tmp_path, "sub", "rg", "storage")
    reference = parse_reference("Ref:sub.rg.storage:storageLocation", tmp_path / "configuration")
    assert reference == Reference("sub", "rg", "storage", "storageLocation")
    assert reference.configuration == "sub/rg/storage.yaml"
    assert reference.deployment_name == "sub.rg.storage"

def test_slash_reference(tmp_path):
    make_config(tmp_path, "sub", "rg", "storage")
    assert parse_reference("Ref:sub/rg/storage:storageLocation", tmp_path / "configuration") == Reference("sub", "rg", "storage", "storageLocation")

@pytest.mark.parametrize("parts, value", [
    (("sub", "rg", "app.v2"), "Ref:sub.rg.app.v2:endpoint"),
    (("sub", "rg.prod", "app"), "Ref:sub.rg.prod.app:endpoint"),
    (("my.sub", "rg", "app"), "Ref:my.sub.rg.app:endpoint"),
    (("sub", "rg", "app.v2"), "Ref:sub/rg/app.v2:endpoint"),
])
def test_names_containing_dots(tmp_path, parts, value):
    make_config(tmp_path, *parts)
    reference = parse_reference(value, tmp_path / "configuration")
    assert (reference.subscription, reference.resource_group, reference.config) == parts
    assert reference.configuration == "/".join(parts) + ".yaml"

def test_ambiguous_dotted_reference(tmp_path):
    make_config(tmp_path, "sub", "rg", "app.v2")
    make_config(tmp_path, "sub", "rg.app", "v2")
    with pytest.raises(InvalidReference) as e:
        parse_reference("Ref:sub.rg.app.v2:endpoint", tmp_path / "configuration")
    assert "is ambiguous, use one of: Ref:sub/rg/app.v2:endpoint, Ref:sub/rg.app/v2:endpoint" in str(e.value)
    # the slash form picks one explicitly
    assert parse_reference("Ref:sub/rg.app/v2:endpoint", tmp_path / "configuration").config == "v2"

def test_reference_to_missing_configuration(tmp_path):
    (tmp_path / "configuration").mkdir()
    with pytest.raises(InvalidReference) as e:
        parse_reference("Ref:sub.rg.storage:storageLocation", tmp_path / "configuration")
    assert "refers to a configuration that does not exist" in str(e.value)
    assert "sub/rg/storage.yaml" in str(e.value)

@pytest.mark.parametrize("value, message", [
    ("Ref:sub.rg.storage", "is missing the output name"),
    ("Ref:sub.rg.storage:", "is not a valid reference"),
    ("Ref::output", "is not a valid reference"),
    ("Ref:sub.storage:output", "is not a valid reference"),
    ("Ref:sub/rg:output", "is not a valid reference"),
    ("Ref:sub/rg/extra/storage:output", "is not a valid reference"),
    ("Ref:sub//storage:output", "is not a valid reference"),
])
def test_malformed_reference(tmp_path, value, message):
    (tmp_path / "configuration").mkdir()
    with pytest.raises(InvalidReference) as e:
        parse_reference(value, tmp_path / "configuration")
    assert message in str(e.value)
    assert "Ref:<subscription>/<resource_group>/<config>:<output>" in str(e.value)
