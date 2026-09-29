from unittest.mock import patch
import sys
import pytest

import nitra
from nitra.lib.orchestrator import Orchestrator
from nitra.lib.subproc import Subproc


def test_login_checked_before_operation():
    calls = []
    with patch.object(sys, 'argv', ["nitra", "deploy", "services-prod/rg-nitra-sample-02/sample_storage.yaml"]), \
         patch.object(Subproc, 'check_azure_login', side_effect = lambda: calls.append("login") or (0, "2026-09-24 18:19:50.000000\n")), \
         patch.object(Orchestrator, 'deploy', side_effect = lambda configuration: calls.append(f"deploy {configuration}")):
        nitra.nitra()
    assert calls == ["login", "deploy services-prod/rg-nitra-sample-02/sample_storage.yaml"]

def test_expired_login_stops_before_operation():
    with patch.object(sys, 'argv', ["nitra", "deploy-account"]), \
         patch.object(Subproc, 'check_azure_login', return_value = (1, "ERROR: AADSTS700082: The refresh token has expired due to inactivity.")), \
         patch.object(Orchestrator, 'deploy_account') as deploy_account:
        with pytest.raises(SystemExit) as e:
            nitra.nitra()
        assert e.value.code == 1
        deploy_account.assert_not_called()

def test_validate_does_not_need_azure_login():
    with patch.object(sys, 'argv', ["nitra", "validate", "services-prod"]), \
         patch.object(Subproc, 'check_azure_login') as check_azure_login, \
         patch.object(Orchestrator, 'validate') as validate:
        nitra.nitra()
    check_azure_login.assert_not_called()
    validate.assert_called_once_with("services-prod")

@pytest.mark.parametrize("flag, expected", [([], False), (["--yes"], True), (["-y"], True)])
def test_yes_flag(flag, expected):
    with patch.object(sys, 'argv', ["nitra", "destroy-account", *flag]), \
         patch.object(Subproc, 'check_azure_login', return_value = (0, "2026-09-29")), \
         patch.object(Orchestrator, 'destroy_account'):
        nitra.nitra()
    assert nitra.orchestrator.assume_yes is expected
