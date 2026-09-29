from unittest.mock import patch
import json

import pytest

from nitra.lib.orchestrator import Orchestrator
from nitra.lib.subproc import Subproc

RG_SCHEMA = "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#"
SUB_SCHEMA = "https://schema.management.azure.com/schemas/2018-05-01/subscriptionDeploymentTemplate.json#"
MG_SCHEMA = "https://schema.management.azure.com/schemas/2019-08-01/managementGroupDeploymentTemplate.json#"

# Compiled templates, as 'az bicep build --stdout' would print them
TEMPLATES = {
    "storage.bicep": {"$schema": RG_SCHEMA, "parameters": {
        "storageName": {"type": "string"},
        "sku": {"type": "string", "defaultValue": "Standard_LRS"},
        "tags": {"type": "object", "nullable": True},
    }},
    "policy.bicep": {"$schema": SUB_SCHEMA, "parameters": {}},
    "mg.bicep": {"$schema": MG_SCHEMA, "parameters": {}},
}

def fake_build(bicep):
    if bicep == "broken.bicep":
        return 1, "WARNING: something minor\nERROR: broken.bicep(2,1) : Error BCP018: Expected the \"=\" character."
    return 0, json.dumps(TEMPLATES[bicep])

def make_project(root, configs, location=True):
    # configs: "sub/rg/name" -> yaml text
    for bicep in list(TEMPLATES) + ["broken.bicep"]:
        (root / "bicep").mkdir(exist_ok=True)
        (root / "bicep" / bicep).write_text("")
    for configuration, text in configs.items():
        folder = root / "configuration" / configuration.rsplit("/", 1)[0]
        folder.mkdir(parents=True, exist_ok=True)
        if location:
            (folder / "location.yaml").write_text("---\nlocation: australiaeast\n")
        (folder / f"{configuration.rsplit('/', 1)[1]}.yaml").write_text(text)

def validate(path=None):
    with patch.object(Subproc, 'build_bicep', side_effect = fake_build) as build:
        try:
            result = Orchestrator().validate(path)
        except SystemExit as e:
            result = e.code
    return result, build

STORAGE = "---\nbicep_path: storage.bicep\nparams:\n  storageName: x\n"

def test_valid_project_passes(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {"sub/rg/a": STORAGE, "sub/rg/b": STORAGE, "sub/rg/p": "---\nbicep_path: policy.bicep\nscope: subscription\n"})
    monkeypatch.chdir(tmp_path)
    result, build = validate()
    assert result is True
    # each template is compiled once, however many configs use it
    assert sorted(c.args[0] for c in build.call_args_list) == ["policy.bicep", "storage.bicep"]
    assert "Validation passed: 3 configuration(s), 2 template(s)" in capfd.readouterr().err

def test_params_checked_against_template(tmp_path, monkeypatch, capfd):
    # optional (defaultValue) and nullable params may be left out, parameter names match without case
    make_project(tmp_path, {
        "sub/rg/typo": "---\nbicep_path: storage.bicep\nparams:\n  storageNmae: x\n",
        "sub/rg/case": "---\nbicep_path: storage.bicep\nparams:\n  STORAGENAME: x\n",
    })
    monkeypatch.chdir(tmp_path)
    result, _ = validate()
    assert result == 1
    err = capfd.readouterr().err
    assert "param 'storageNmae' is not a parameter of bicep/storage.bicep" in err
    assert "param 'storageName' is required by bicep/storage.bicep but not set" in err
    assert "case.yaml" not in err
    assert "sku" not in err and "tags" not in err

def test_scope_checked_against_template(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {
        "sub/rg/policy": "---\nbicep_path: policy.bicep\n",
        "sub/rg/storage": "---\nbicep_path: storage.bicep\nscope: subscription\nparams:\n  storageName: x\n",
        "sub/rg/mg": "---\nbicep_path: mg.bicep\n",
    })
    monkeypatch.chdir(tmp_path)
    assert validate()[0] == 1
    err = capfd.readouterr().err
    assert "bicep/policy.bicep has targetScope = 'subscription', set 'scope: subscription'" in err
    assert "bicep/storage.bicep deploys to a resource group, remove 'scope: subscription'" in err
    assert "bicep/mg.bicep targets a scope Nitra does not deploy to" in err

def test_compile_error_reported_once(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {"sub/rg/a": "---\nbicep_path: broken.bicep\n", "sub/rg/b": "---\nbicep_path: broken.bicep\n"})
    monkeypatch.chdir(tmp_path)
    assert validate()[0] == 1
    err = capfd.readouterr().err
    assert err.count("does not compile") == 1
    assert "Error BCP018" in err
    assert "something minor" not in err

def test_everything_reported_together(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {
        "sub/rg/a": "---\nbicep_path: storage.bicep\nparams:\n  storageName: Ref:sub/rg/b:out\n",
        "sub/rg/b": "---\nbicep_path: storage.bicep\nparams:\n  storageName: Ref:sub/rg/a:out\n",
        "sub/rg/badref": "---\nbicep_path: storage.bicep\nparams:\n  storageName: Ref:sub/rg/missing:out\n",
        "sub/rg/badyaml": "---\nbicep_path: [unclosed\n",
        "sub/rg/nobicep": "---\nbicep_path: missing.bicep\n",
    })
    (tmp_path / "configuration" / "sub" / "rg-empty-location").mkdir()
    (tmp_path / "configuration" / "sub" / "rg-empty-location" / "location.yaml").write_text("")
    (tmp_path / "configuration" / "sub" / "rg-empty-location" / "app.yaml").write_text(STORAGE)
    monkeypatch.chdir(tmp_path)
    assert validate()[0] == 1
    err = capfd.readouterr().err
    assert err.count("circular reference: ") == 1
    assert "circular reference: sub/rg/a.yaml -> sub/rg/b.yaml -> sub/rg/a.yaml" in err
    assert "refers to a configuration that does not exist" in err
    assert "invalid YAML" in err
    assert "'bicep_path' file not found: bicep/missing.bicep" in err
    assert "configuration/sub/rg-empty-location/location.yaml\n  - 'location' is required" in err

def test_missing_location_file(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {"sub/rg/a": STORAGE}, location=False)
    monkeypatch.chdir(tmp_path)
    assert validate()[0] == 1
    assert "configuration/sub/rg/location.yaml\n  - file not found" in capfd.readouterr().err

@pytest.mark.parametrize("path, expected", [
    ("sub-a", 1), ("sub-a/rg", 1), ("sub-a/rg/a.yaml", 1), ("sub-b", True),
])
def test_validate_scope(tmp_path, monkeypatch, path, expected):
    make_project(tmp_path, {"sub-a/rg/a": "---\nbicep_path: storage.bicep\n", "sub-b/rg/b": STORAGE})
    monkeypatch.chdir(tmp_path)
    assert validate(path)[0] == expected

@pytest.mark.parametrize("path", ["nope", "sub/rg/extra/level"])
def test_validate_unknown_path(tmp_path, monkeypatch, capfd, path):
    make_project(tmp_path, {"sub/rg/a": STORAGE})
    monkeypatch.chdir(tmp_path)
    assert validate(path)[0] == 1
    assert "Nothing to validate at" in capfd.readouterr().err

def test_no_configuration_folder(tmp_path, monkeypatch, capfd):
    monkeypatch.chdir(tmp_path)
    assert validate()[0] == 1
    assert "No configuration folder found" in capfd.readouterr().err
