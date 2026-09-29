from unittest.mock import patch
import sys

import pytest

from nitra.lib.deployer import Deployer
from nitra.lib.orchestrator import Orchestrator


def make_project(root):
    resource_group = root / "configuration" / "sub" / "rg"
    resource_group.mkdir(parents=True)
    (resource_group / "location.yaml").write_text("---\nlocation: australiaeast\n")
    (resource_group / "storage.yaml").write_text("---\nbicep_path: template.bicep\n")
    (resource_group / "app.yaml").write_text(
        "---\nbicep_path: template.bicep\naction_on_unmanage: detachAll\nparams:\n  x: Ref:sub/rg/storage:output\n")

def run_destroy(monkeypatch, is_terminal, answer=None, assume_yes=False):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: is_terminal)
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    orchestrator = Orchestrator()
    orchestrator.assume_yes = assume_yes
    with patch.object(Orchestrator, 'stack_exists', return_value = True), \
         patch.object(Deployer, 'destroy_bicep') as destroy_bicep:
        try:
            orchestrator.destroy_resource_group("sub/rg")
            exit_code = None
        except SystemExit as e:
            exit_code = e.code
    return [c.args[1] for c in destroy_bicep.call_args_list], prompts, exit_code

def test_lists_what_will_be_destroyed_and_asks(tmp_path, monkeypatch, capfd):
    make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    destroyed, prompts, exit_code = run_destroy(monkeypatch, is_terminal=True, answer="yes")
    assert exit_code is None
    assert prompts == ["Type 'yes' to destroy these stacks: "]
    assert destroyed == ["sub.rg.app", "sub.rg.storage"]
    err = capfd.readouterr().err
    assert "Destroying 2 stack(s), in this order:" in err
    assert "1. sub/rg/app.yaml (detaches its resources, they are kept)" in err
    assert "2. sub/rg/storage.yaml (deletes its resources)" in err

@pytest.mark.parametrize("answer", ["", "y", "no", "YES please"])
def test_anything_but_yes_cancels(tmp_path, monkeypatch, capfd, answer):
    make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    destroyed, _, exit_code = run_destroy(monkeypatch, is_terminal=True, answer=answer)
    assert exit_code == 1
    assert destroyed == []
    assert "Destroy cancelled, nothing was destroyed" in capfd.readouterr().err

def test_no_terminal_refuses_without_yes(tmp_path, monkeypatch, capfd):
    make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    destroyed, prompts, exit_code = run_destroy(monkeypatch, is_terminal=False)
    assert exit_code == 1
    assert destroyed == [] and prompts == []
    assert "pass --yes to confirm" in capfd.readouterr().err

@pytest.mark.parametrize("is_terminal", [True, False])
def test_assume_yes_skips_the_prompt(tmp_path, monkeypatch, capfd, is_terminal):
    make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    destroyed, prompts, exit_code = run_destroy(monkeypatch, is_terminal=is_terminal, assume_yes=True)
    assert exit_code is None and prompts == []
    assert destroyed == ["sub.rg.app", "sub.rg.storage"]
    # the list is still shown, so CI logs record what was destroyed
    assert "Destroying 2 stack(s)" in capfd.readouterr().err

def test_nothing_to_destroy_does_not_ask(tmp_path, monkeypatch, capfd):
    (tmp_path / "configuration" / "sub" / "rg").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("should not ask"))
    Orchestrator().destroy_resource_group("sub/rg")
    assert "Nothing to destroy" in capfd.readouterr().err
