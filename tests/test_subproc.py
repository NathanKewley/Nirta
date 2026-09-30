from unittest.mock import patch
import pytest
import json
import subprocess

from nitra.lib.subproc import Subproc


subproc = Subproc()

def completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)

def test_run_command():
    sample_azure_response = open('tests/test_output/test_subproc_run_command.json', 'r').read()
    with patch("subprocess.run", return_value = completed([], 0, sample_azure_response, "")) as run:
        run_command_result = json.loads(subproc.run_command(["az", "stack", "group", "show", "--name", "test", "--resource-group", "rg"]))
        run.assert_called_once_with(["az", "stack", "group", "show", "--name", "test", "--resource-group", "rg"], capture_output=True, text=True, check=False, env=None)
    assert run_command_result['name'] == "nitra-sample.rg-nitra-sample-01.nitra_automation_account"
    assert run_command_result['properties']['provisioningState'] == "Succeeded"

def test_run_command_ignores_stderr_warnings():
    with patch("subprocess.run", return_value = completed([], 0, '{"name": "services-prod"}', "WARNING: A new version of Azure CLI is available.")):
        assert json.loads(subproc.run_command(["az", "account", "show", "--output", "json"])) == {"name": "services-prod"}

def test_run_command_with_exit_code():
    with patch("subprocess.run", return_value = completed([], 1, "out", "ERROR: failed")):
        assert subproc.run_command_with_exit_code(["az", "stack", "group", "create"]) == (1, "outERROR: failed")

def test_run_command_streamed_keeps_spaces_in_arguments(tmp_path):
    script = tmp_path / "hook with spaces.sh"
    script.write_text("[ \"$1\" = \"value with spaces\" ]\n")
    assert subproc.run_command_streamed(["sh", str(script), "value with spaces"]) == 0
    assert subproc.run_command_streamed(["sh", str(script), "other"]) != 0

def test_get_stack_resource_group():
    with patch("subprocess.run", return_value = completed([], 0, "{}", "WARNING: something")) as run:
        assert subproc.get_stack("sub.rg.config", "rg", "sub-id") == (0, "{}")
        assert run.call_args[0][0] == ["az", "stack", "group", "show", "--name", "sub.rg.config", "--resource-group", "rg", "--subscription", "sub-id", "--output", "json"]

def test_get_stack_subscription():
    with patch("subprocess.run", return_value = completed([], 0, "{}")) as run:
        assert subproc.get_stack("sub.policy.config", None, "sub-id") == (0, "{}")
        assert run.call_args[0][0] == ["az", "stack", "sub", "show", "--name", "sub.policy.config", "--subscription", "sub-id", "--output", "json"]

def test_deploy_group_create():
    with patch("subprocess.run", return_value = completed([], 0, "{}")) as run:
        assert subproc.deploy_group_create("storage/storage_account.bicep", "rg", "sub.rg.config", "deleteResources", "None", "/tmp/nitra-params.json", "sub-id") == (0, "{}")
        command = run.call_args[0][0]
        assert command[:6] == ["az", "stack", "group", "create", "-f", "bicep/storage/storage_account.bicep"]
        assert command[command.index("--parameters") + 1] == "@/tmp/nitra-params.json"
        assert command[command.index("--subscription") + 1] == "sub-id"

def test_deploy_subscription_create():
    with patch("subprocess.run", return_value = completed([], 0, "{}")) as run:
        assert subproc.deploy_subscription_create("policy/assignAllowedLocations.bicep", "sub.policy.config", "deleteResources", "None", "/tmp/nitra-params.json", "australiaeast", "sub-id") == (0, "{}")
        command = run.call_args[0][0]
        assert command[:4] == ["az", "stack", "sub", "create"]
        assert command[command.index("--parameters") + 1] == "@/tmp/nitra-params.json"
        assert command[command.index("--location") + 1] == "australiaeast"
        assert command[command.index("--subscription") + 1] == "sub-id"

def test_deploy_group_create_path_with_spaces():
    with patch("subprocess.run", return_value = completed([], 0, "{}")) as run:
        subproc.deploy_group_create("My Templates/storage account.bicep", "rg", "sub.rg.config", "deleteResources", "None", "C:\\Users\\Jane Doe\\Temp\\nitra-params.json", "sub-id")
        command = run.call_args[0][0]
        assert command[command.index("-f") + 1] == "bicep/My Templates/storage account.bicep"
        assert command[command.index("--parameters") + 1] == "@C:\\Users\\Jane Doe\\Temp\\nitra-params.json"
        assert "" not in command

def test_hooks_pass_script_as_one_argument():
    from nitra.lib.hooks.BashScript import Hook as BashHook
    from nitra.lib.hooks.Python3Script import Hook as PythonHook
    with patch("subprocess.run", return_value = completed([], 0)) as run:
        BashHook(subproc.logger, "my hook.sh").execute_hook()
        assert run.call_args[0][0] == ["sh", "scripts/my hook.sh"]
        assert run.call_args[1]["capture_output"] is False
        PythonHook(subproc.logger, "my hook.py").execute_hook()
        assert run.call_args[0][0] == ["python3", "scripts/my hook.py"]

def test_az_not_installed():
    with patch("subprocess.run", side_effect = FileNotFoundError(2, "No such file or directory", "az")):
        with pytest.raises(SystemExit) as e:
            subproc.list_subscriptions()
        assert e.value.code == 1

def test_hook_interpreter_not_installed():
    with patch("subprocess.run", side_effect = FileNotFoundError(2, "No such file or directory", "python3")):
        with pytest.raises(SystemExit) as e:
            subproc.run_command_streamed(["python3", "scripts/hook.py"])
        assert e.value.code == 1

def test_resource_group_exists():
    with patch("subprocess.run", return_value = completed([], 0, "true\n", "WARNING: something")) as run:
        assert subproc.resource_group_exists("rg", "sub-id") == (0, "true\n")
        assert run.call_args[0][0] == ["az", "group", "exists", "--name", "rg", "--subscription", "sub-id"]

def test_resource_group_exists_returns_error_on_failure():
    with patch("subprocess.run", return_value = completed([], 1, "", "ERROR: AADSTS700082: The refresh token has expired")):
        assert subproc.resource_group_exists("rg", "sub-id") == (1, "ERROR: AADSTS700082: The refresh token has expired")

def test_check_azure_login_only_returns_expiry():
    with patch("subprocess.run", return_value = completed([], 0, "2026-09-24 18:19:50.000000\n")) as run:
        assert subproc.check_azure_login() == (0, "2026-09-24 18:19:50.000000\n")
        assert run.call_args[0][0] == ["az", "account", "get-access-token", "--query", "expiresOn", "--output", "tsv"]

def test_hook_output_is_shown(tmp_path, monkeypatch, capfd):
    from nitra.lib.hooks.BashScript import Hook as BashHook
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "hook.sh").write_text("echo 'hook stdout line'\necho 'hook stderr line' >&2\n")
    monkeypatch.chdir(tmp_path)
    BashHook(subproc.logger, "hook.sh").execute_hook()
    captured = capfd.readouterr()
    assert "hook stdout line" in captured.out
    assert "hook stderr line" in captured.err

def test_failing_hook_exits_with_error(tmp_path, monkeypatch, capfd):
    from nitra.lib.hooks.Python3Script import Hook as PythonHook
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "hook.py").write_text("import sys\nprint('checking prerequisites')\nprint('prerequisite missing', file=sys.stderr)\nsys.exit(3)\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        PythonHook(subproc.logger, "hook.py").execute_hook()
    assert e.value.code == 1
    captured = capfd.readouterr()
    assert "checking prerequisites" in captured.out
    assert "prerequisite missing" in captured.err
    assert "Python3 Hook failed with exit code 3: hook.py" in captured.err

def test_hooks_receive_deployment_environment(tmp_path, monkeypatch, capfd):
    from nitra.lib.hooks.BashScript import Hook as BashHook
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "hook.sh").write_text('echo "target=$NITRA_SUBSCRIPTION_ID/$NITRA_RESOURCE_GROUP path=$PATH"\n')
    monkeypatch.chdir(tmp_path)
    BashHook(subproc.logger, "hook.sh", {"NITRA_SUBSCRIPTION_ID": "sub-id", "NITRA_RESOURCE_GROUP": "rg"}).execute_hook()
    out = capfd.readouterr().out
    assert "target=sub-id/rg" in out
    # the rest of the environment is kept, so the script can still find az, python3, etc.
    assert "path=/" in out

def test_build_bicep_compiles_locally():
    with patch("subprocess.run", return_value = completed([], 0, "{}", "WARNING: linter")) as run:
        assert subproc.build_bicep("storage/storage_account.bicep") == (0, "{}")
        assert run.call_args[0][0] == ["az", "bicep", "build", "--file", "bicep/storage/storage_account.bicep", "--stdout"]
