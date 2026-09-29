import sys
import argparse
from argparse import RawTextHelpFormatter

from nitra.lib.logger import Logger as logger
from nitra.lib.orchestrator import Orchestrator

logger = logger.get_logger()
orchestrator = Orchestrator()


def _parse_args():
    parser = argparse.ArgumentParser(prog='nitra', formatter_class=RawTextHelpFormatter, 
    description="""nitra Usage:
        - deploy: deploy a single configuration
        - deploy-resource-group: deploy all config in a specific resource group
        - deploy-subscription: deploy all config in a specific subscription
        - deploy-account: deploy all config in the account / nitra project
        - destroy: destroy a single configuration
        - destroy-resource-group: destroy all config in a specific resource group
        - destroy-subscription: destroy all config in a specific subscription
        - destroy-account: destroy all config in the account / nitra project
        - validate [path]: check config and templates without deploying, for the whole project,
          a subscription, a resource group or one config. Does not need an Azure login
        - plan [path]: preview what a deploy would create, modify, delete or detach, without
          changing anything or running hooks. Same paths as validate
        destroy commands list what will be destroyed and ask for confirmation, use --yes to skip it
        see GitHub for more details: https://github.com/NathanKewley/nitra """)
    parser.add_argument('operation', nargs=1, help=argparse.SUPPRESS, choices=[ "deploy", "deploy-resource-group", "deploy-subscription", "deploy-account", "destroy", "destroy-resource-group", "destroy-subscription", "destroy-account", "validate", "plan"], metavar="operation")
    parser.add_argument('suboperation', nargs='?', default=None, help=argparse.SUPPRESS)
    parser.add_argument('-y', '--yes', action='store_true', help="destroy without asking for confirmation, e.g. in CI")
    args = parser.parse_args()
    args.operation[0] = args.operation[0].replace("-", "_")
    return args

def nitra():
    args = _parse_args()
    logger.debug(args)
    orchestrator.assume_yes = args.yes
    try:
        # Fail before any hooks or deployments run if the Azure login is missing or expired,
        # validate works offline so it can run in CI without credentials
        if args.operation[0] != "validate":
            orchestrator.subscription.check_azure_login()
        if args.suboperation is None:
            getattr(orchestrator, f"{args.operation[0]}")()
        else:
            getattr(orchestrator, f"{args.operation[0]}")(args.suboperation)
    except Exception as e:
        logger.error(e, exc_info=True)
        sys.exit(1)
