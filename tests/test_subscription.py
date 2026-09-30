from unittest.mock import patch
import json
import pytest
import pathlib
import subprocess

from nitra.lib.subproc import Subproc
from nitra.lib.subscription import Subscription


SUBSCRIPTIONS = json.dumps([
    {"name": "services-prod", "id": "2222"},
    {"name": "cloudops-prod", "id": "3333"},
    {"name": "dev", "id": "4444"},
    {"name": "dev", "id": "5555"},
])

def test_get_subscription_id():
    with patch.object(Subproc, 'list_subscriptions', return_value = (0, SUBSCRIPTIONS)) as list_subscriptions:
        subscription = Subscription(Subproc())
        assert subscription.get_subscription_id("services-prod") == "2222"
        assert subscription.get_subscription_id("cloudops-prod") == "3333"
        # listed once per run, not once per lookup
        list_subscriptions.assert_called_once()

def test_subscription_id_as_folder_name():
    with patch.object(Subproc, 'list_subscriptions', return_value = (0, SUBSCRIPTIONS)):
        assert Subscription(Subproc()).get_subscription_id("3333") == "3333"

def test_subscription_not_found(capfd):
    with patch.object(Subproc, 'list_subscriptions', return_value = (0, SUBSCRIPTIONS)):
        with pytest.raises(SystemExit) as e:
            Subscription(Subproc()).get_subscription_id("services-prdo")
    assert e.value.code == 1
    assert "Subscription not found: 'services-prdo'. Subscriptions available to this login: cloudops-prod, dev, services-prod" in capfd.readouterr().err

def test_duplicate_subscription_name(capfd):
    with patch.object(Subproc, 'list_subscriptions', return_value = (0, SUBSCRIPTIONS)):
        with pytest.raises(SystemExit) as e:
            Subscription(Subproc()).get_subscription_id("dev")
    assert e.value.code == 1
    assert "More than one subscription is named 'dev' (4444, 5555), use the subscription id as the folder name instead" in capfd.readouterr().err

def test_list_subscriptions_not_logged_in(capfd):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="ERROR: Please run 'az login' to setup account.")

    with patch("subprocess.run", side_effect = fake_run) as run:
        with pytest.raises(SystemExit) as e:
            Subscription(Subproc()).get_subscription_id("services-prod")
    assert e.value.code == 1
    assert run.call_args[0][0] == ["az", "account", "list", "--output", "json"]
    assert "Unable to list Azure subscriptions" in capfd.readouterr().err

def test_lookup_ignores_az_warnings_and_never_switches():
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=SUBSCRIPTIONS, stderr="WARNING: A new version of Azure CLI is available.")

    with patch("subprocess.run", side_effect = fake_run) as run:
        assert Subscription(Subproc()).get_subscription_id("services-prod") == "2222"
    commands = [c.args[0] for c in run.call_args_list]
    assert commands == [["az", "account", "list", "--output", "json"]]

def test_check_azure_login():
    with patch.object(Subproc, 'check_azure_login', return_value = (0, "2026-09-24 18:19:50.000000\n")):
        subscription = Subscription(Subproc())
        subscription.check_azure_login()

def test_check_azure_login_expired():
    expired = "ERROR: AADSTS700082: The refresh token has expired due to inactivity."
    with patch.object(Subproc, 'check_azure_login', return_value = (1, expired)):
        subscription = Subscription(Subproc())
        with pytest.raises(SystemExit) as e:
            subscription.check_azure_login()
        assert e.value.code == 1

def make_az_config(root):
    # A fake az config folder shaped like the real one, with the UTF-8 BOM az writes
    config = root / "real-az-config"
    (config / "bin").mkdir(parents=True)
    (config / "bin" / "bicep").write_text("bicep binary")
    (config / "msal_token_cache.json").write_text('{"tokens": "secret"}')
    (config / "config").write_text("[core]\n")
    profile = {"installationId": "x", "subscriptions": [
        {"id": "2222", "name": "services-prod", "isDefault": True},
        {"id": "3333", "name": "cloudops-prod", "isDefault": False},
    ]}
    (config / "azureProfile.json").write_text(json.dumps(profile), encoding="utf-8-sig")
    return config

def read_profile(folder):
    return json.loads((folder / "azureProfile.json").read_text(encoding="utf-8-sig"))

def test_az_config_for_hook(tmp_path, monkeypatch):
    real = make_az_config(tmp_path)
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(real))
    subscription = Subscription(Subproc())
    with subscription.az_config_for("3333") as config_dir:
        hook_config = pathlib.Path(config_dir)
        # the hook's default is the config's subscription
        assert [s["name"] for s in read_profile(hook_config)["subscriptions"] if s["isDefault"]] == ["cloudops-prod"]
        # everything else is the real login, linked not copied
        for entry in ["bin", "msal_token_cache.json", "config"]:
            assert (hook_config / entry).is_symlink()
            assert (hook_config / entry).resolve() == (real / entry).resolve()
        assert not (hook_config / "azureProfile.json").is_symlink()
    # the temporary folder is removed, the real one is untouched
    assert not hook_config.exists()
    assert [s["name"] for s in read_profile(real)["subscriptions"] if s["isDefault"]] == ["services-prod"]
    assert (real / "bin" / "bicep").read_text() == "bicep binary"
    assert (real / "msal_token_cache.json").read_text() == '{"tokens": "secret"}'

def test_az_config_for_without_az_login(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(tmp_path))
    with Subscription(Subproc()).az_config_for("3333") as config_dir:
        assert config_dir is None

def test_az_config_for_unknown_subscription(tmp_path, monkeypatch):
    real = make_az_config(tmp_path)
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(real))
    with Subscription(Subproc()).az_config_for("9999") as config_dir:
        assert config_dir is None
    assert read_profile(real)["subscriptions"][0]["isDefault"] is True
