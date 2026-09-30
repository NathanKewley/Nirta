import json
import pytest

from nitra.lib.orchestrator import Orchestrator


orchestrator = Orchestrator()

def test_deploy_load_config2():
    config_path = "services-prod/rg-nitra-sample-01/nitra_automation_account.yaml"
    config_expected_loaded_output = json.loads(open('tests/test_output/test_deploy_load_config.json', 'r').read())

    config_loaded = orchestrator.load_config(config_path)
    assert type(config_loaded) == dict
    assert config_loaded["bicep_path"] == "automation/automation_account.bicep"
    assert config_loaded["params"]["location"] == "Ref:services-prod.rg-nitra-sample-01.nitra_automation_storage:storageLocation"
    assert config_loaded["params"]["appName"] == "nitraAutomation"
    assert config_loaded["params"]["skuName"] == "Free"
    assert config_loaded == config_expected_loaded_output

def test_deploy_load_location2():
    config_path = "services-prod/rg-nitra-sample-01/nitra_automation_account.yaml"
    config_expected_loaded_location = "australiaeast"

    location_config_loaded = orchestrator.load_location(config_path)
    assert type(location_config_loaded) == str
    assert location_config_loaded == config_expected_loaded_location


def make_project(root, config_text, location_text="---\nlocation: australiaeast\n"):
    resource_group = root / "configuration" / "sub" / "rg"
    resource_group.mkdir(parents=True)
    (root / "bicep").mkdir()
    (root / "bicep" / "storage.bicep").write_text("")
    (root / "scripts").mkdir()
    (root / "scripts" / "hook.sh").write_text("exit 0\n")
    if location_text is not None:
        (resource_group / "location.yaml").write_text(location_text)
    if config_text is not None:
        (resource_group / "app.yaml").write_text(config_text)

def test_valid_config_without_params(tmp_path, monkeypatch):
    make_project(tmp_path, "---\nbicep_path: storage.bicep\npre_hooks:\n  BashScript: hook.sh\n")
    monkeypatch.chdir(tmp_path)
    config = Orchestrator().load_config("sub/rg/app.yaml")
    assert config["params"] == {}
    assert config["pre_hooks"] == {"BashScript": "hook.sh"}

def test_empty_params_becomes_empty_mapping(tmp_path, monkeypatch):
    make_project(tmp_path, "---\nbicep_path: storage.bicep\nparams:\n")
    monkeypatch.chdir(tmp_path)
    assert Orchestrator().load_config("sub/rg/app.yaml")["params"] == {}

def test_all_problems_reported_together(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, """---
bicep_path: missing.bicep
scope: subscriptoin
params:
  - not
  - a mapping
pre_hooks:
  BashScrpt: hook.sh
post_hooks:
  Python3Script: missing.py
""")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        Orchestrator().load_config("sub/rg/app.yaml")
    assert e.value.code == 1
    err = capfd.readouterr().err
    assert "Invalid configuration: configuration/sub/rg/app.yaml" in err
    assert "'scope' must be 'resource_group' or 'subscription', got 'subscriptoin'" in err
    assert "'params' must be a mapping of parameter names to values" in err
    assert "'bicep_path' file not found: bicep/missing.bicep" in err
    assert "unknown hook type 'BashScrpt' in 'pre_hooks', expected one of: BashScript, Python3Script" in err
    assert "'post_hooks' script not found: scripts/missing.py" in err

def test_missing_bicep_path(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, "---\nparams:\n  name: test\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        Orchestrator().load_config("sub/rg/app.yaml")
    assert "'bicep_path' is required" in capfd.readouterr().err

def test_destroy_does_not_need_template_or_scripts(tmp_path, monkeypatch):
    make_project(tmp_path, "---\nbicep_path: removed.bicep\npre_hooks:\n  BashScript: removed.sh\n")
    monkeypatch.chdir(tmp_path)
    config = Orchestrator().load_config("sub/rg/app.yaml", deploy_mode="destroy")
    assert config["bicep_path"] == "removed.bicep"

def test_unknown_setting_warns(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, "---\nbicep_path: storage.bicep\npre_hook:\n  BashScript: hook.sh\n")
    monkeypatch.chdir(tmp_path)
    Orchestrator().load_config("sub/rg/app.yaml")
    assert "Unknown setting 'pre_hook' in configuration/sub/rg/app.yaml will be ignored" in capfd.readouterr().err

@pytest.mark.parametrize("config_text, message", [
    ("", "must be a mapping of settings"),
    ("---\n- bicep_path: storage.bicep\n", "must be a mapping of settings"),
    ("---\nbicep_path: [unclosed\n", "invalid YAML"),
])
def test_unusable_config_file(tmp_path, monkeypatch, capfd, config_text, message):
    make_project(tmp_path, config_text)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        Orchestrator().load_config("sub/rg/app.yaml")
    assert e.value.code == 1
    assert message in capfd.readouterr().err

def test_config_file_not_found(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, None)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        Orchestrator().load_config("sub/rg/app.yaml")
    assert "Invalid configuration: configuration/sub/rg/app.yaml\n  - file not found" in capfd.readouterr().err

@pytest.mark.parametrize("location_text, message", [
    (None, "file not found, every resource group folder needs one"),
    ("---\nregion: australiaeast\n", "'location' is required"),
    ("", "'location' is required"),
])
def test_bad_location_file(tmp_path, monkeypatch, capfd, location_text, message):
    make_project(tmp_path, "---\nbicep_path: storage.bicep\n", location_text)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        Orchestrator().load_location("sub/rg/app.yaml")
    assert e.value.code == 1
    err = capfd.readouterr().err
    assert "configuration/sub/rg/location.yaml" in err
    assert message in err

def test_bad_references_reported_on_load(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, """---
bicep_path: storage.bicep
params:
  location: Ref:sub.rg.missing:storageLocation
  name: Ref:sub.rg.app
  plain: not a reference
""")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        Orchestrator().load_config("sub/rg/app.yaml")
    assert e.value.code == 1
    err = capfd.readouterr().err
    assert "param 'location': 'Ref:sub.rg.missing:storageLocation' refers to a configuration that does not exist" in err
    assert "param 'name': 'Ref:sub.rg.app' is missing the output name" in err

def test_references_not_checked_on_destroy(tmp_path, monkeypatch):
    make_project(tmp_path, "---\nbicep_path: storage.bicep\nparams:\n  location: Ref:sub.rg.removed:storageLocation\n")
    monkeypatch.chdir(tmp_path)
    Orchestrator().load_config("sub/rg/app.yaml", deploy_mode="destroy")
