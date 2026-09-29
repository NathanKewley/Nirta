from unittest.mock import patch
import json

from nitra.lib.deployer import Deployer
from nitra.lib.hook_orchestrator import HookOrchestrator
from nitra.lib.orchestrator import Orchestrator
from nitra.lib.subproc import Subproc

RG_ID = "/subscriptions/id-sub/resourceGroups/rg/providers"
STORAGE = f"{RG_ID}/Microsoft.Storage/storageAccounts/store"
SITE = f"{RG_ID}/Microsoft.Web/sites/app"
OLD_NSG = f"{RG_ID}/Microsoft.Network/networkSecurityGroups/old"
OTHER = f"{RG_ID}/Microsoft.Compute/virtualMachines/not-ours"


def make_project(root, configs):
    # configs: "sub/rg/name" -> yaml text
    (root / "bicep").mkdir()
    (root / "bicep" / "template.bicep").write_text("")
    (root / "scripts").mkdir()
    (root / "scripts" / "hook.sh").write_text("exit 0\n")
    for configuration, text in configs.items():
        folder = root / "configuration" / configuration.rsplit("/", 1)[0]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "location.yaml").write_text("---\nlocation: australiaeast\n")
        (folder / f"{configuration.rsplit('/', 1)[1]}.yaml").write_text(text)

def what_if(*changes):
    return 0, json.dumps({"status": "Succeeded", "error": None, "changes": list(changes)})

def change(change_type, resource_id, delta=None):
    return {"changeType": change_type, "resourceId": resource_id, "delta": delta}

def stack(resources=(), outputs=None):
    return 0, json.dumps({"resources": [{"id": r, "status": "managed"} for r in resources], "outputs": outputs or {}})

def run_plan(tmp_path, monkeypatch, stacks=None, what_ifs=None, rg_exists=True, path=None):
    # stacks: current stacks by name. what_ifs: results handed out in the order what-if is called
    # (deploy order), keyed by the stack each is meant for so the tests read clearly
    monkeypatch.chdir(tmp_path)
    stacks = stacks or {}
    what_ifs = what_ifs or {}
    seen_parameters = {}

    def fake_group_what_if(bicep, resource_group, parameters_file, subscription_id):
        seen_parameters[len(seen_parameters)] = json.loads(open(parameters_file).read())["parameters"]
        return what_ifs.pop(next(iter(what_ifs)))

    def fake_sub_what_if(bicep, location, parameters_file, subscription_id):
        seen_parameters[len(seen_parameters)] = json.loads(open(parameters_file).read())["parameters"]
        return what_ifs.pop(next(iter(what_ifs)))

    with patch.object(Subproc, 'get_stack', side_effect = lambda name, rg, sub: stacks.get(name, (3, ""))), \
         patch.object(Subproc, 'resource_group_exists', return_value = (0, "true" if rg_exists else "false")), \
         patch.object(Subproc, 'what_if_group', side_effect = fake_group_what_if) as group_what_if, \
         patch.object(Subproc, 'what_if_subscription', side_effect = fake_sub_what_if) as sub_what_if, \
         patch.object(HookOrchestrator, 'run_hooks') as run_hooks, \
         patch.object(Deployer, 'deploy_bicep') as deploy_bicep, \
         patch.object(Deployer, 'deploy_bicep_subscription') as deploy_sub:
        try:
            exit_code = Orchestrator().plan(path)
        except SystemExit as e:
            exit_code = e.code
    # planning never deploys or runs hooks
    run_hooks.assert_not_called()
    deploy_bicep.assert_not_called()
    deploy_sub.assert_not_called()
    return exit_code, group_what_if, sub_what_if, seen_parameters

APP = "---\nbicep_path: template.bicep\npre_hooks:\n  BashScript: hook.sh\nparams:\n  name: app\n"

def test_creates_modifies_and_unchanged(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {"sub/rg/app": APP})
    delta = [{"path": "properties.siteConfig", "propertyChangeType": "Modify", "children": [
        {"path": "alwaysOn", "propertyChangeType": "Modify", "before": False, "after": True},
        {"path": "http20Enabled", "propertyChangeType": "Create", "after": True},
        {"path": "ftpsState", "propertyChangeType": "Delete", "before": "AllAllowed"},
        {"path": "noise", "propertyChangeType": "NoEffect", "after": 1},
    ]}]
    result, *_ = run_plan(tmp_path, monkeypatch, stacks={"sub.rg.app": stack([STORAGE, SITE])}, what_ifs={"sub.rg.app": what_if(
        change("Create", f"{RG_ID}/Microsoft.Network/publicIPAddresses/ip"),
        change("Modify", SITE, delta),
        change("NoChange", STORAGE),
        change("Ignore", OTHER),
    )})
    assert result is True
    err = capfd.readouterr().err
    assert "sub/rg/app.yaml (stack sub.rg.app)" in err
    assert "  + Microsoft.Network/publicIPAddresses/ip" in err
    assert "  ~ Microsoft.Web/sites/app" in err
    assert "~ alwaysOn: false => true" in err
    assert "+ http20Enabled: true" in err
    assert '- ftpsState: "AllAllowed"' in err
    assert "noise" not in err
    assert "= 1 unchanged" in err
    # resources that are in the resource group but not in the template are not this stack's business
    assert "not-ours" not in err
    assert "Plan: 1 to create, 1 to modify, 1 unchanged" in err

def test_resources_removed_from_template_are_deleted_by_the_stack(tmp_path, monkeypatch, capfd):
    # what-if reports a resource that still exists but is no longer in the template as 'Ignore',
    # the stack will still delete it, so it must be shown
    make_project(tmp_path, {"sub/rg/app": APP})
    run_plan(tmp_path, monkeypatch, stacks={"sub.rg.app": stack([SITE, OLD_NSG.upper()])},
             what_ifs={"sub.rg.app": what_if(change("NoChange", SITE), change("Ignore", OLD_NSG))})
    err = capfd.readouterr().err
    assert "no longer in the template, the stack deletes it" in err
    assert "Plan: 1 to delete, 1 unchanged" in err

def test_removed_resources_are_detached_with_detach_all(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {"sub/rg/app": APP.replace("params:", "action_on_unmanage: detachAll\nparams:")})
    run_plan(tmp_path, monkeypatch, stacks={"sub.rg.app": stack([SITE, OLD_NSG])}, what_ifs={"sub.rg.app": what_if(change("NoChange", SITE))})
    err = capfd.readouterr().err
    assert "the stack detaches it, it is kept" in err
    assert "Plan: 1 to detach, 1 unchanged" in err

def test_refs_use_current_outputs(tmp_path, monkeypatch):
    make_project(tmp_path, {
        "sub/rg/storage": "---\nbicep_path: template.bicep\n",
        "sub/rg/app": "---\nbicep_path: template.bicep\nparams:\n  location: Ref:sub/rg/storage:storageLocation\n",
    })
    result, group_what_if, _, seen = run_plan(tmp_path, monkeypatch,
        stacks={"sub.rg.storage": stack(outputs={"storageLocation": {"value": "australiaeast"}})},
        what_ifs={"sub.rg.storage": what_if(), "sub.rg.app": what_if()})
    assert group_what_if.call_count == 2
    assert seen[1] == {"location": {"value": "australiaeast"}}

def test_undeployed_dependency_cannot_be_previewed(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {
        "sub/rg/storage": "---\nbicep_path: template.bicep\n",
        "sub/rg/app": "---\nbicep_path: template.bicep\nparams:\n  location: Ref:sub/rg/storage:storageLocation\n",
    })
    result, group_what_if, _, _ = run_plan(tmp_path, monkeypatch, what_ifs={"sub.rg.storage": what_if(change("Create", STORAGE))})
    assert result is True
    assert group_what_if.call_count == 1
    err = capfd.readouterr().err
    assert "sub/rg/storage.yaml is not deployed yet, so its output 'storageLocation' is not known" in err
    assert "1 configuration(s) could not be previewed" in err

def test_new_output_not_yet_deployed(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {
        "sub/rg/storage": "---\nbicep_path: template.bicep\n",
        "sub/rg/app": "---\nbicep_path: template.bicep\nparams:\n  endpoint: Ref:sub/rg/storage:newOutput\n",
    })
    run_plan(tmp_path, monkeypatch, stacks={"sub.rg.storage": stack(outputs={"old": {"value": 1}})},
             what_ifs={"sub.rg.storage": what_if()})
    assert "has no output 'newOutput' yet" in capfd.readouterr().err

def test_missing_resource_group_cannot_be_previewed(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {"sub/rg/app": APP})
    result, group_what_if, _, _ = run_plan(tmp_path, monkeypatch, rg_exists=False)
    group_what_if.assert_not_called()
    assert "resource group rg does not exist yet, it would be created in australiaeast" in capfd.readouterr().err

def test_subscription_scope_uses_subscription_what_if(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {"sub/policy/assign": "---\nbicep_path: template.bicep\nscope: subscription\n"})
    policy = "/subscriptions/id-sub/providers/Microsoft.Authorization/policyAssignments/allowed"
    _, group_what_if, sub_what_if, _ = run_plan(tmp_path, monkeypatch, what_ifs={"sub.policy.assign": what_if(change("Create", policy))})
    group_what_if.assert_not_called()
    assert sub_what_if.call_args.args[1] == "australiaeast"
    assert "+ Microsoft.Authorization/policyAssignments/allowed" in capfd.readouterr().err

def test_what_if_failure_fails_the_plan_after_the_rest(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {"sub/rg/a": APP, "sub/rg/b": APP})
    result, group_what_if, _, _ = run_plan(tmp_path, monkeypatch, what_ifs={
        "sub.rg.a": (1, "ERROR: InvalidTemplate: something is wrong"),
        "sub.rg.b": what_if(change("Create", STORAGE)),
    })
    assert result == 1
    assert group_what_if.call_count == 2
    err = capfd.readouterr().err
    assert "InvalidTemplate: something is wrong" in err
    assert "Plan: 1 to create" in err
    assert "What-if failed for 1 configuration(s): sub/rg/a.yaml" in err

def test_dependency_outside_scope_is_planned_or_skipped(tmp_path, monkeypatch, capfd):
    configs = {
        "sub/rg-shared/storage": "---\nbicep_path: template.bicep\n",
        "sub/rg-app/app": "---\nbicep_path: template.bicep\nparams:\n  location: Ref:sub/rg-shared/storage:loc\n",
    }
    make_project(tmp_path, configs)
    deployed = {"sub.rg-shared.storage": (0, json.dumps({"provisioningState": "succeeded", "resources": [], "outputs": {"loc": {"value": "x"}}}))}
    # planned by default, as deploy would redeploy it
    _, group_what_if, _, _ = run_plan(tmp_path, monkeypatch, stacks=deployed, what_ifs={"a": what_if(), "b": what_if()}, path="sub/rg-app")
    assert group_what_if.call_count == 2
    capfd.readouterr()
    # not planned when it opts out of being redeployed and is already deployed
    (tmp_path / "configuration" / "sub" / "rg-shared" / "storage.yaml").write_text("---\nbicep_path: template.bicep\nredeploy_as_dependency: false\n")
    _, group_what_if, _, _ = run_plan(tmp_path, monkeypatch, stacks=deployed, what_ifs={"b": what_if()}, path="sub/rg-app")
    assert group_what_if.call_count == 1
    assert "not redeployed, it is already deployed and redeploy_as_dependency is false" in capfd.readouterr().err

def test_no_changes(tmp_path, monkeypatch, capfd):
    make_project(tmp_path, {"sub/rg/app": APP})
    run_plan(tmp_path, monkeypatch, stacks={"sub.rg.app": stack([SITE])}, what_ifs={"sub.rg.app": what_if(change("NoChange", SITE))})
    assert "Plan: 1 unchanged" in capfd.readouterr().err
