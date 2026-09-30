import subprocess

import pytest

_real_run = subprocess.run


@pytest.fixture(autouse=True)
def block_real_az(monkeypatch):
    # The Azure CLI on a developer machine is usually logged in to a real tenant, so a test that
    # reaches the real 'az' fails instead of quietly talking to Azure. Tests that mock
    # subprocess.run themselves replace this guard for their duration
    def guarded_run(command, *args, **kwargs):
        if command and command[0] == "az":
            raise AssertionError(f"test tried to run the real Azure CLI: {' '.join(command)}")
        return _real_run(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", guarded_run)


@pytest.fixture(autouse=True)
def fake_subscription_ids(request, monkeypatch):
    # Every subscription name resolves to a predictable fake id, except in the tests of the lookup itself
    if request.module.__name__.endswith("test_subscription"):
        return
    from nitra.lib.subscription import Subscription
    monkeypatch.setattr(Subscription, "get_subscription_id", lambda self, name: f"id-{name}")


@pytest.fixture(autouse=True)
def isolated_az_config(tmp_path_factory, monkeypatch):
    # Never read or link the developer's real ~/.azure, tests that need an az config folder build their own
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(tmp_path_factory.mktemp("empty-az-config")))
