import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

from nitra.lib.logger import Logger as logger


class Subscription():

    def __init__(self, subproc):
        self.logger = logger.get_logger()
        self.logger.propagate = False
        self.subproc = subproc
        # Subscription name -> list of ids, loaded on first use
        self.subscription_ids = None

    def check_azure_login(self):
        returncode, output = self.subproc.check_azure_login()
        if returncode != 0:
            self.logger.error(f"Unable to authenticate with Azure, run 'az login'.\n{output.strip()}")
            sys.exit(1)

    def get_subscription_id(self, subscription_name):
        # Looked up once per run from the local az login. The id is then passed to each az command with
        # --subscription, rather than switching the user's default subscription with 'az account set'
        if self.subscription_ids is None:
            returncode, output = self.subproc.list_subscriptions()
            if returncode != 0:
                self.logger.error(f"Unable to list Azure subscriptions, are you logged in? Run 'az login'.\n{output.strip()}")
                sys.exit(1)
            self.subscription_ids = {}
            for subscription in json.loads(output):
                self.subscription_ids.setdefault(subscription["name"], []).append(subscription["id"])

        # A folder can also be named with the subscription id itself
        all_ids = [i for ids in self.subscription_ids.values() for i in ids]
        if subscription_name in all_ids:
            return subscription_name
        ids = self.subscription_ids.get(subscription_name, [])
        if not ids:
            available = ", ".join(sorted(self.subscription_ids)) or "none"
            self.logger.error(f"Subscription not found: '{subscription_name}'. Subscriptions available to this login: {available}")
            sys.exit(1)
        if len(ids) > 1:
            self.logger.error(f"More than one subscription is named '{subscription_name}' ({', '.join(ids)}), use the subscription id as the folder name instead")
            sys.exit(1)
        return ids[0]

    @contextmanager
    def az_config_for(self, subscription_id):
        # az has no environment variable for the default subscription, so hooks get their own az config folder
        # (AZURE_CONFIG_DIR) whose default is the config's subscription. It links to everything in the real
        # folder (login, token cache, extensions, bicep) except azureProfile.json, which is copied with the
        # default changed. The user's real default subscription is never changed
        source = os.environ.get("AZURE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".azure")
        profile_path = os.path.join(source, "azureProfile.json")
        if not os.path.isfile(profile_path):
            yield None
            return
        config_dir = tempfile.mkdtemp(prefix="nitra-az-")
        try:
            try:
                ready = self._prepare_az_config(source, profile_path, config_dir, subscription_id)
            except OSError as e:
                self.logger.warning(f"Unable to set up the az config for hooks, their az commands will use your default subscription: {e}")
                ready = False
            yield config_dir if ready else None
        finally:
            # rmtree removes the links themselves, it does not follow them into the real config folder
            shutil.rmtree(config_dir, ignore_errors=True)

    def _prepare_az_config(self, source, profile_path, config_dir, subscription_id):
        # azureProfile.json is written by az with a UTF-8 byte order mark
        with open(profile_path, encoding="utf-8-sig") as file:
            profile = json.load(file)
        subscriptions = profile.get("subscriptions", [])
        if not any(subscription.get("id") == subscription_id for subscription in subscriptions):
            self.logger.warning(f"Subscription {subscription_id} is not in the az profile, hook az commands will use your default subscription")
            return False
        for subscription in subscriptions:
            subscription["isDefault"] = subscription.get("id") == subscription_id
        for entry in os.listdir(source):
            if entry != "azureProfile.json":
                os.symlink(os.path.join(source, entry), os.path.join(config_dir, entry))
        with open(os.path.join(config_dir, "azureProfile.json"), "w", encoding="utf-8-sig") as file:
            json.dump(profile, file)
        return True
