from unittest.mock import patch
import json
import os
import subprocess
import sys
import pytest

from nitra.lib.orchestrator import Orchestrator
from nitra.lib.deployer import Deployer
from nitra.lib.subproc import Subproc
from nitra.lib.subscription import Subscription
from nitra.lib.hook_orchestrator import HookOrchestrator



@pytest.fixture(autouse=True)
def confirm_destroys(monkeypatch):
    # These tests cover what is destroyed and in what order, the confirmation itself is tested in test_destroy_confirmation.py
    monkeypatch.setattr(Orchestrator, "confirm_destroy", lambda self, destroy_order: None)

orchestrator = Orchestrator()

def test_get_deployment_name():
    config_path = "services-prod/rg-nitra-sample-01/nitra_automation_account.yaml"
    result = orchestrator.get_deployment_name(config_path)
    assert result == "services-prod.rg-nitra-sample-01.nitra_automation_account"

def test_get_resource_group():
    config_path = "services-prod/rg-nitra-sample-01/nitra_automation_account.yaml"
    result = orchestrator.get_resource_group(config_path)
    assert result == "rg-nitra-sample-01"

def test_get_subscription():
    config_path = "services-prod/rg-nitra-sample-01/nitra_automation_account.yaml"
    result = orchestrator.get_subscription(config_path)
    assert result == "services-prod"

def test_get_child_directories():
    path = "configuration/services-prod/"
    result = orchestrator.get_child_directories(path)
    assert result == ["policy", "rg-nitra-sample-01", "rg-nitra-sample-02"]

def make_messy_configuration(root):
    resource_group = root / "configuration" / "sub-a" / "rg-a"
    resource_group.mkdir(parents=True)
    (root / "configuration" / "sub-b" / "rg-b").mkdir(parents=True)
    (root / "configuration" / ".hidden-sub").mkdir()
    (root / "configuration" / "README.md").write_text("docs")
    (root / "configuration" / "sub-a" / "notes.txt").write_text("notes")
    (root / "configuration" / "sub-a" / ".DS_Store").write_text("")
    for name in ["location.yaml", "b_storage.yaml", "a_network.yaml", "legacy.yml", "README.md", ".DS_Store", ".hidden.yaml"]:
        (resource_group / name).write_text("---\n")
    (resource_group / "subfolder.yaml").mkdir()
    (resource_group / "archive").mkdir()

def test_get_configurations_skips_non_configurations(tmp_path, monkeypatch, caplog):
    make_messy_configuration(tmp_path)
    monkeypatch.chdir(tmp_path)
    orchestrator = Orchestrator()
    orchestrator.logger.propagate = True
    with caplog.at_level("WARNING", logger="logging"):
        assert orchestrator.get_configurations("configuration/sub-a/rg-a/") == ["a_network.yaml", "b_storage.yaml"]
    assert "legacy.yml: configuration files must use the .yaml extension" in caplog.text
    assert "README.md" not in caplog.text

def test_deploy_walkers_skip_stray_files(tmp_path, monkeypatch):
    make_messy_configuration(tmp_path)
    monkeypatch.chdir(tmp_path)
    orchestrator = Orchestrator()
    assert orchestrator.deploy_account(dry_run=True) == ["sub-a", "sub-b"]
    assert orchestrator.deploy_subscription("sub-a", dry_run=True) == ["sub-a/rg-a"]
    assert orchestrator.deploy_resource_group("sub-a/rg-a", dry_run=True) == ["sub-a/rg-a/a_network.yaml", "sub-a/rg-a/b_storage.yaml"]

def test_deploy_resource_group_only_deploys_configurations(tmp_path, monkeypatch):
    make_messy_configuration(tmp_path)
    monkeypatch.chdir(tmp_path)
    orchestrator = Orchestrator()
    with patch.object(Orchestrator, 'deploy') as deploy:
        orchestrator.deploy_resource_group("sub-a/rg-a", deploy_mode="destroy")
    assert [c.args[0] for c in deploy.call_args_list] == ["sub-a/rg-a/a_network.yaml", "sub-a/rg-a/b_storage.yaml"]

def test_deploy():
    configuration = "services-prod/rg-nitra-sample-01/nitra_automation_account.yaml"
    result = Orchestrator().deploy(configuration, dry_run=True)
    assert result[0] == {'location': 'Ref:services-prod.rg-nitra-sample-01.nitra_automation_storage:storageLocation', 'appName': 'nitraAutomation', 'skuName': 'Free'}
    assert result[1] == "automation/automation_account.bicep"
    assert result[2] == "rg-nitra-sample-01"
    assert result[3] == "australiaeast"
    assert result[4] == "services-prod.rg-nitra-sample-01.nitra_automation_account"
    assert result[5] == "services-prod"

def test_deploy_resource_group():
    configuration = "services-prod/rg-nitra-sample-01"
    result = orchestrator.deploy_resource_group(configuration, dry_run=True)
    assert sorted(result) == [
        "services-prod/rg-nitra-sample-01/nitra_automation_account.yaml",
        "services-prod/rg-nitra-sample-01/nitra_automation_storage.yaml",
        "services-prod/rg-nitra-sample-01/nitra_module_virtualnetwork.yaml"
    ]

def test_deploy_subscription():
    configuration = "services-prod"
    result = orchestrator.deploy_subscription(configuration, dry_run=True)
    assert sorted(result) == ["services-prod/policy", "services-prod/rg-nitra-sample-01", "services-prod/rg-nitra-sample-02"]

def test_deploy_account():
    result = orchestrator.deploy_account(dry_run=True)
    assert result == ["services-prod"]

def test_destroy_subscription_scope():
    orchestrator = Orchestrator()
    with patch.object(Orchestrator, 'stack_exists', return_value = True) as stack_exists, \
         patch.object(Deployer, 'destroy_bicep_subscription') as destroy_subscription, \
         patch.object(Deployer, 'destroy_bicep') as destroy_group:
        orchestrator.destroy("services-prod/policy/allowed-locations.yaml")
        stack_exists.assert_called_with("services-prod.policy.allowed-locations", "policy", "services-prod", "subscription")
        destroy_subscription.assert_called_once_with("services-prod.policy.allowed-locations", "services-prod", "deleteResources")
        destroy_group.assert_not_called()

def test_destroy_resource_group_scope():
    orchestrator = Orchestrator()
    with patch.object(Orchestrator, 'stack_exists', return_value = True) as stack_exists, \
         patch.object(Deployer, 'destroy_bicep_subscription') as destroy_subscription, \
         patch.object(Deployer, 'destroy_bicep') as destroy_group:
        orchestrator.destroy("services-prod/rg-nitra-sample-02/sample_storage.yaml")
        stack_exists.assert_called_with("services-prod.rg-nitra-sample-02.sample_storage", "rg-nitra-sample-02", "services-prod", "resource_group")
        destroy_group.assert_called_once_with("rg-nitra-sample-02", "services-prod.rg-nitra-sample-02.sample_storage", "services-prod", "deleteResources")
        destroy_subscription.assert_not_called()

def test_destroy_missing_stack_is_skipped():
    orchestrator = Orchestrator()
    with patch.object(Orchestrator, 'stack_exists', return_value = False), \
         patch.object(Deployer, 'destroy_bicep_subscription') as destroy_subscription, \
         patch.object(Deployer, 'destroy_bicep') as destroy_group:
        orchestrator.destroy("services-prod/policy/allowed-locations.yaml")
        destroy_subscription.assert_not_called()
        destroy_group.assert_not_called()

def test_stack_exists_subscription_scope_has_no_resource_group():
    orchestrator = Orchestrator()
    with patch.object(Subproc, 'get_stack', return_value = (0, "{}")) as get_stack:
        assert orchestrator.stack_exists("services-prod.policy.allowed-locations", "policy", "services-prod", "subscription")
        get_stack.assert_called_with("services-prod.policy.allowed-locations", None, "id-services-prod")

def test_stack_exists_not_found():
    orchestrator = Orchestrator()
    with patch.object(Subproc, 'get_stack', return_value = (3, "")) as get_stack:
        assert not orchestrator.stack_exists("services-prod.rg-nitra-sample-02.sample_storage", "rg-nitra-sample-02", "services-prod", "resource_group")
        get_stack.assert_called_with("services-prod.rg-nitra-sample-02.sample_storage", "rg-nitra-sample-02", "id-services-prod")

def test_deploy_invalid_scope(tmp_path, monkeypatch):
    resource_group = tmp_path / "configuration" / "sub" / "rg"
    resource_group.mkdir(parents=True)
    (tmp_path / "bicep").mkdir()
    (tmp_path / "bicep" / "storage.bicep").write_text("")
    (resource_group / "location.yaml").write_text("---\nlocation: australiaeast\n")
    (resource_group / "app.yaml").write_text("---\nbicep_path: storage.bicep\nscope: subscriptoin\n")
    monkeypatch.chdir(tmp_path)
    orchestrator = Orchestrator()
    with patch.object(Deployer, 'deploy_bicep') as deploy_group, \
         patch.object(Deployer, 'deploy_bicep_subscription') as deploy_subscription:
        with pytest.raises(SystemExit) as e:
            orchestrator.deploy("sub/rg/app.yaml")
        assert e.value.code == 1
        deploy_group.assert_not_called()
        deploy_subscription.assert_not_called()

def test_dependency_with_dotted_file_name(tmp_path, monkeypatch):
    resource_group = tmp_path / "configuration" / "sub" / "rg"
    resource_group.mkdir(parents=True)
    (tmp_path / "bicep").mkdir()
    (tmp_path / "bicep" / "storage.bicep").write_text("")
    (resource_group / "location.yaml").write_text("---\nlocation: australiaeast\n")
    (resource_group / "storage.v2.yaml").write_text("---\nbicep_path: storage.bicep\n")
    (resource_group / "app.yaml").write_text("---\nbicep_path: storage.bicep\nparams:\n  location: Ref:sub.rg.storage.v2:storageLocation\n")
    monkeypatch.chdir(tmp_path)
    orchestrator = Orchestrator()
    with patch.object(Deployer, 'deploy_bicep') as deploy_bicep:
        orchestrator.deploy("sub/rg/app.yaml")
    # the dependency deploys first, from the right file and with the right stack name
    assert [c.args[4] for c in deploy_bicep.call_args_list] == ["sub.rg.storage.v2", "sub.rg.app"]

def make_reference_project(root, references):
    # references: config name -> list of config names it takes an output from
    resource_group = root / "configuration" / "sub" / "rg"
    resource_group.mkdir(parents=True)
    (root / "bicep").mkdir()
    (root / "bicep" / "template.bicep").write_text("")
    (root / "scripts").mkdir()
    (root / "scripts" / "hook.sh").write_text("exit 0\n")
    (resource_group / "location.yaml").write_text("---\nlocation: australiaeast\n")
    for name, dependencies in references.items():
        params = "".join(f"  from_{dependency}: Ref:sub.rg.{dependency}:output\n" for dependency in dependencies)
        (resource_group / f"{name}.yaml").write_text(
            f"---\nbicep_path: template.bicep\npre_hooks:\n  BashScript: hook.sh\nparams:\n  name: {name}\n{params}")

def deployed_configs(deploy_bicep):
    return [c.args[4] for c in deploy_bicep.call_args_list]

def test_circular_reference_stops_before_anything_runs(tmp_path, monkeypatch, capfd):
    # b also depends on an unrelated config that would otherwise deploy before the cycle is hit
    make_reference_project(tmp_path, {"a": ["b"], "b": ["unrelated", "a"], "unrelated": []})
    monkeypatch.chdir(tmp_path)
    with patch.object(Deployer, 'deploy_bicep') as deploy_bicep, \
         patch.object(HookOrchestrator, 'run_hooks') as run_hooks:
        with pytest.raises(SystemExit) as e:
            Orchestrator().deploy("sub/rg/a.yaml")
    assert e.value.code == 1
    deploy_bicep.assert_not_called()
    run_hooks.assert_not_called()
    assert "Circular reference between configurations:\n  sub/rg/a.yaml\n  -> sub/rg/b.yaml\n  -> sub/rg/a.yaml" in capfd.readouterr().err

def test_self_reference(tmp_path, monkeypatch, capfd):
    make_reference_project(tmp_path, {"a": ["a"]})
    monkeypatch.chdir(tmp_path)
    with patch.object(Deployer, 'deploy_bicep') as deploy_bicep:
        with pytest.raises(SystemExit):
            Orchestrator().deploy("sub/rg/a.yaml")
    deploy_bicep.assert_not_called()
    assert "sub/rg/a.yaml\n  -> sub/rg/a.yaml" in capfd.readouterr().err

def test_circular_reference_in_bulk_deploy(tmp_path, monkeypatch, capfd):
    make_reference_project(tmp_path, {"a": ["b"], "b": ["c"], "c": ["a"]})
    monkeypatch.chdir(tmp_path)
    with patch.object(Deployer, 'deploy_bicep') as deploy_bicep, \
         patch.object(HookOrchestrator, 'run_hooks'):
        with pytest.raises(SystemExit):
            Orchestrator().deploy_resource_group("sub/rg")
    deploy_bicep.assert_not_called()
    assert "sub/rg/a.yaml\n  -> sub/rg/b.yaml\n  -> sub/rg/c.yaml\n  -> sub/rg/a.yaml" in capfd.readouterr().err

def test_shared_dependency_is_not_circular(tmp_path, monkeypatch):
    # a -> b -> d and a -> c -> d is a diamond, not a cycle
    make_reference_project(tmp_path, {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []})
    monkeypatch.chdir(tmp_path)
    with patch.object(Deployer, 'deploy_bicep') as deploy_bicep, \
         patch.object(HookOrchestrator, 'run_hooks'):
        Orchestrator().deploy("sub/rg/a.yaml")
    assert deployed_configs(deploy_bicep) == ["sub.rg.d", "sub.rg.b", "sub.rg.c", "sub.rg.a"]

def test_destroy_ignores_references(tmp_path, monkeypatch):
    make_reference_project(tmp_path, {"a": ["b"], "b": ["a"]})
    monkeypatch.chdir(tmp_path)
    with patch.object(Orchestrator, 'stack_exists', return_value = True), \
         patch.object(Deployer, 'destroy_bicep') as destroy_bicep:
        Orchestrator().destroy_resource_group("sub/rg")
    assert [c.args[1] for c in destroy_bicep.call_args_list] == ["sub.rg.a", "sub.rg.b"]

def make_multi_folder_project(root, references):
    # references: "sub/rg/name" -> list of "sub/rg/name" it takes an output from
    (root / "bicep").mkdir()
    (root / "bicep" / "template.bicep").write_text("")
    for configuration, dependencies in references.items():
        folder = root / "configuration" / os.path.dirname(configuration)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "location.yaml").write_text("---\nlocation: australiaeast\n")
        params = "".join(f"  from_{i}: Ref:{dependency}:output\n" for i, dependency in enumerate(dependencies))
        (folder / f"{os.path.basename(configuration)}.yaml").write_text(f"---\nbicep_path: template.bicep\nparams:\n  name: x\n{params}")

def destroyed_configs(destroy_bicep):
    return [c.args[1] for c in destroy_bicep.call_args_list]

def run_destroy(method, *args):
    with patch.object(Orchestrator, 'stack_exists', return_value = True), \
         patch.object(Deployer, 'destroy_bicep') as destroy_bicep:
        getattr(Orchestrator(), method)(*args)
    return destroyed_configs(destroy_bicep)

def test_destroy_resource_group_reverse_dependency_order(tmp_path, monkeypatch):
    # alphabetical would be network, storage, vm, which destroys the network while the vm still uses it
    make_multi_folder_project(tmp_path, {
        "sub/rg/network": [],
        "sub/rg/storage": [],
        "sub/rg/vm": ["sub/rg/network", "sub/rg/storage"],
    })
    monkeypatch.chdir(tmp_path)
    order = run_destroy("destroy_resource_group", "sub/rg")
    assert order[0] == "sub.rg.vm"
    assert sorted(order) == ["sub.rg.network", "sub.rg.storage", "sub.rg.vm"]

def test_destroy_chain_order(tmp_path, monkeypatch):
    make_multi_folder_project(tmp_path, {"sub/rg/a": [], "sub/rg/b": ["sub/rg/a"], "sub/rg/c": ["sub/rg/b"]})
    monkeypatch.chdir(tmp_path)
    assert run_destroy("destroy_resource_group", "sub/rg") == ["sub.rg.c", "sub.rg.b", "sub.rg.a"]

def test_destroy_subscription_orders_across_resource_groups(tmp_path, monkeypatch):
    make_multi_folder_project(tmp_path, {
        "sub/rg-a-network/vnet": [],
        "sub/rg-b-app/app": ["sub/rg-a-network/vnet"],
    })
    monkeypatch.chdir(tmp_path)
    assert run_destroy("destroy_subscription", "sub") == ["sub.rg-b-app.app", "sub.rg-a-network.vnet"]

def test_destroy_account_orders_across_subscriptions(tmp_path, monkeypatch):
    make_multi_folder_project(tmp_path, {
        "sub-a/rg/shared": [],
        "sub-b/rg/app": ["sub-a/rg/shared"],
    })
    monkeypatch.chdir(tmp_path)
    assert run_destroy("destroy_account") == ["sub-b.rg.app", "sub-a.rg.shared"]

def test_destroy_ignores_reference_to_removed_config(tmp_path, monkeypatch):
    make_multi_folder_project(tmp_path, {"sub/rg/a": ["sub/rg/removed"], "sub/rg/b": []})
    monkeypatch.chdir(tmp_path)
    assert sorted(run_destroy("destroy_resource_group", "sub/rg")) == ["sub.rg.a", "sub.rg.b"]

def test_destroy_warns_about_dependents_left_behind(tmp_path, monkeypatch, capfd):
    make_multi_folder_project(tmp_path, {
        "sub/rg-shared/storage": [],
        "sub/rg-app/app": ["sub/rg-shared/storage"],
    })
    monkeypatch.chdir(tmp_path)
    assert run_destroy("destroy_resource_group", "sub/rg-shared") == ["sub.rg-shared.storage"]
    assert "sub/rg-app/app.yaml takes outputs from sub/rg-shared/storage.yaml, which is being destroyed" in capfd.readouterr().err

def test_destroy_not_blocked_by_broken_config_elsewhere(tmp_path, monkeypatch):
    make_multi_folder_project(tmp_path, {"sub/rg-a/a": [], "sub/rg-b/b": []})
    (tmp_path / "configuration" / "sub" / "rg-b" / "broken.yaml").write_text("---\nparams: [unclosed\n")
    monkeypatch.chdir(tmp_path)
    assert run_destroy("destroy_resource_group", "sub/rg-a") == ["sub.rg-a.a"]

def test_destroy_checks_every_config_before_destroying(tmp_path, monkeypatch, capfd):
    make_multi_folder_project(tmp_path, {"sub/rg/a": [], "sub/rg/b": []})
    (tmp_path / "configuration" / "sub" / "rg" / "c.yaml").write_text("---\nscope: subscriptoin\n")
    monkeypatch.chdir(tmp_path)
    with patch.object(Orchestrator, 'stack_exists', return_value = True), \
         patch.object(Deployer, 'destroy_bicep') as destroy_bicep:
        with pytest.raises(SystemExit):
            Orchestrator().destroy_resource_group("sub/rg")
    destroy_bicep.assert_not_called()
    assert "configuration/sub/rg/c.yaml" in capfd.readouterr().err

def make_shared_storage_project(root, storage_settings="", app_folder="sub/rg"):
    # storage is a shared dependency with a pre-hook, app takes an output from it
    (root / "bicep").mkdir()
    (root / "bicep" / "template.bicep").write_text("")
    (root / "scripts").mkdir()
    (root / "scripts" / "hook.sh").write_text("exit 0\n")
    for folder in {"sub/rg", app_folder}:
        (root / "configuration" / folder).mkdir(parents=True, exist_ok=True)
        (root / "configuration" / folder / "location.yaml").write_text("---\nlocation: australiaeast\n")
    (root / "configuration" / "sub" / "rg" / "storage.yaml").write_text(
        f"---\nbicep_path: template.bicep\n{storage_settings}pre_hooks:\n  BashScript: hook.sh\nparams:\n  name: storage\n")
    (root / "configuration" / app_folder / "app.yaml").write_text(
        "---\nbicep_path: template.bicep\nparams:\n  location: Ref:sub/rg/storage:storageLocation\n")

def run_deploy(method, *args, stack_state="succeeded"):
    stack = (0, f'{{"provisioningState": "{stack_state}"}}') if stack_state else (3, "")
    with patch.object(Subproc, 'get_stack', return_value = stack), \
         patch.object(Deployer, 'deploy_bicep') as deploy_bicep, \
         patch.object(HookOrchestrator, 'run_hooks') as run_hooks:
        getattr(Orchestrator(), method)(*args)
    return deployed_configs(deploy_bicep), run_hooks.call_count

def test_dependency_redeployed_by_default(tmp_path, monkeypatch):
    make_shared_storage_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    deployed, hooks_run = run_deploy("deploy", "sub/rg/app.yaml")
    assert deployed == ["sub.rg.storage", "sub.rg.app"]
    assert hooks_run == 1

def test_dependency_skipped_when_opted_out_and_deployed(tmp_path, monkeypatch, capfd):
    make_shared_storage_project(tmp_path, "redeploy_as_dependency: false\n")
    monkeypatch.chdir(tmp_path)
    deployed, hooks_run = run_deploy("deploy", "sub/rg/app.yaml")
    assert deployed == ["sub.rg.app"]
    assert hooks_run == 0
    assert "Skipping sub/rg/storage.yaml: already deployed and redeploy_as_dependency is false" in capfd.readouterr().err

@pytest.mark.parametrize("stack_state", [None, "failed", "deploying"])
def test_opted_out_dependency_deployed_when_not_successfully_deployed(tmp_path, monkeypatch, stack_state):
    make_shared_storage_project(tmp_path, "redeploy_as_dependency: false\n")
    monkeypatch.chdir(tmp_path)
    deployed, hooks_run = run_deploy("deploy", "sub/rg/app.yaml", stack_state=stack_state)
    assert deployed == ["sub.rg.storage", "sub.rg.app"]
    assert hooks_run == 1

def test_opted_out_dependency_deployed_when_explicitly_requested(tmp_path, monkeypatch):
    make_shared_storage_project(tmp_path, "redeploy_as_dependency: false\n")
    monkeypatch.chdir(tmp_path)
    deployed, _ = run_deploy("deploy", "sub/rg/storage.yaml")
    assert deployed == ["sub.rg.storage"]

@pytest.mark.parametrize("method, args", [
    ("deploy_resource_group", ("sub/rg",)),
    ("deploy_subscription", ("sub",)),
    ("deploy_account", ()),
])
def test_opted_out_dependency_deployed_when_in_bulk_scope(tmp_path, monkeypatch, method, args):
    # app sorts before storage, so storage is first reached as a dependency, it must still deploy, and before app
    make_shared_storage_project(tmp_path, "redeploy_as_dependency: false\n")
    monkeypatch.chdir(tmp_path)
    deployed, hooks_run = run_deploy(method, *args)
    assert deployed == ["sub.rg.storage", "sub.rg.app"]
    assert hooks_run == 1

def test_opted_out_dependency_outside_bulk_scope_is_skipped(tmp_path, monkeypatch):
    make_shared_storage_project(tmp_path, "redeploy_as_dependency: false\n", app_folder="sub/rg-app")
    monkeypatch.chdir(tmp_path)
    deployed, hooks_run = run_deploy("deploy_resource_group", "sub/rg-app")
    assert deployed == ["sub.rg-app.app"]
    assert hooks_run == 0

def test_redeploy_as_dependency_must_be_boolean(tmp_path, monkeypatch, capfd):
    make_shared_storage_project(tmp_path, "redeploy_as_dependency: no-thanks\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        Orchestrator().load_config("sub/rg/storage.yaml")
    assert "'redeploy_as_dependency' must be true or false" in capfd.readouterr().err

def test_full_run_never_switches_default_subscription(tmp_path, monkeypatch):
    # Records every az command from a deploy then destroy of a two-subscription project
    make_multi_folder_project(tmp_path, {"sub-a/rg/shared": [], "sub-b/rg/app": ["sub-a/rg/shared"]})
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "hook.sh").write_text("exit 0\n")
    app = tmp_path / "configuration" / "sub-b" / "rg" / "app.yaml"
    app.write_text(app.read_text().replace("params:", "post_hooks:\n  BashScript: hook.sh\nparams:"))
    monkeypatch.chdir(tmp_path)
    commands, hook_environments = [], []

    def fake_run(command, **kwargs):
        if command[0] == "sh":
            hook_environments.append(kwargs["env"])
            return subprocess.CompletedProcess(command, 0)
        commands.append(command)
        stdout = "true" if command[1:3] == ["group", "exists"] else '{"outputs": {"output": {"value": "x"}}}'
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    with patch("subprocess.run", side_effect = fake_run):
        Orchestrator().deploy_account()
        Orchestrator().destroy_account()

    assert commands
    assert not [c for c in commands if c[1:3] == ["account", "set"]]
    for command in commands:
        assert command[command.index("--subscription") + 1] in ("id-sub-a", "id-sub-b"), command
    # the stack in sub-b is created and destroyed in sub-b, its Ref: output is read from sub-a
    assert any(c[1:4] == ["stack", "group", "show"] and "sub-a.rg.shared" in c and "id-sub-a" in c for c in commands)
    assert any(c[1:4] == ["stack", "group", "create"] and "sub-b.rg.app" in c and "id-sub-b" in c for c in commands)
    assert any(c[1:4] == ["stack", "group", "delete"] and "sub-b.rg.app" in c and "id-sub-b" in c for c in commands)
    # the post-hook is told where the deployment went
    assert hook_environments[0]["NITRA_SUBSCRIPTION"] == "sub-b"
    assert hook_environments[0]["NITRA_SUBSCRIPTION_ID"] == "id-sub-b"
    assert hook_environments[0]["NITRA_RESOURCE_GROUP"] == "rg"
    assert hook_environments[0]["NITRA_CONFIGURATION"] == "sub-b/rg/app.yaml"

def test_unknown_subscription_fails_before_hooks(tmp_path, monkeypatch):
    make_shared_storage_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    def unknown(self, name):
        sys.exit(1)

    monkeypatch.setattr(Subscription, "get_subscription_id", unknown)
    with patch.object(HookOrchestrator, 'run_hooks') as run_hooks, \
         patch.object(Deployer, 'deploy_bicep') as deploy_bicep:
        with pytest.raises(SystemExit):
            Orchestrator().deploy("sub/rg/storage.yaml")
    run_hooks.assert_not_called()
    deploy_bicep.assert_not_called()

def test_hook_az_commands_default_to_config_subscription(tmp_path, monkeypatch, capfd):
    make_shared_storage_project(tmp_path)
    # a stand in az that reports the default subscription from whichever config folder it is given
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    (bin_dir / "az").write_text(
        "#!/bin/sh\n"
        "python3 -c \"import json,os; p=json.load(open(os.path.join(os.environ['AZURE_CONFIG_DIR'],'azureProfile.json'),encoding='utf-8-sig')); "
        "print('hook az default:', [s['name'] for s in p['subscriptions'] if s['isDefault']][0], 'telemetry:', os.environ.get('AZURE_CORE_COLLECT_TELEMETRY'))\"\n")
    (bin_dir / "az").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    real_config = tmp_path / "real-az-config"
    real_config.mkdir()
    (real_config / "azureProfile.json").write_text(json.dumps({"subscriptions": [
        {"id": "id-other", "name": "other", "isDefault": True},
        {"id": "id-sub", "name": "sub", "isDefault": False},
    ]}), encoding="utf-8-sig")
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(real_config))
    (tmp_path / "scripts" / "hook.sh").write_text("az account show\n")
    monkeypatch.chdir(tmp_path)

    with patch.object(Deployer, 'deploy_bicep'):
        Orchestrator().deploy("sub/rg/storage.yaml")

    # and az telemetry is off, so no background upload writes into the removed temporary folder
    assert "hook az default: sub telemetry: false" in capfd.readouterr().out
    # the user's own default is unchanged
    assert json.loads((real_config / "azureProfile.json").read_text(encoding="utf-8-sig"))["subscriptions"][0]["isDefault"] is True
