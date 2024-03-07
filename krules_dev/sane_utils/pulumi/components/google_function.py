import hashlib
import os
import re
from typing import List, Mapping, Any, Tuple

from pulumi import Output
from pulumi_gcp.cloudfunctionsv2 import FunctionServiceConfigSecretVolumeArgs

from krules_dev import sane_utils
from krules_dev.sane_utils import inject

import pulumi
import pulumi_gcp as gcp

from krules_dev.sane_utils.pulumi.components import GoogleServiceAccount


class GoogleFunction(pulumi.ComponentResource):

    @inject
    def __init__(self, resource_name: str,
                 target: str = None,
                 project_name: str = None,
                 project_id: str = None,
                 region: str = None,
                 source_bucket: gcp.storage.Bucket = None,
                 source_dir: str = "./src",
                 sa: gcp.serviceaccount.Account | Output[gcp.serviceaccount.Account] = None,
                 build_config_kwargs: Mapping[str, Any] = None,
                 service_config_kwargs: Mapping[str, Any] = None,
                 # require_authentication: bool = True,
                 access_secrets: List[str | Tuple[str, str]] = None,
                 access_secrets_volumes: Mapping[str, str] = None,
                 publish_to: Mapping[str, gcp.pubsub.Topic] = None,
                 # subscribe_to: Mapping[str, Mapping[str, Any]] = None,
                 subscribe_to: gcp.pubsub.Topic = None,
                 use_firestore: bool = None,
                 opts: pulumi.ResourceOptions = None,
                 **extra_kwargs
                 ) -> None:
        super().__init__('sane:gcp:FunctionGen2', resource_name, None, opts)

        if service_config_kwargs is None:
            service_config_kwargs = {}

        if access_secrets is None:
            access_secrets = []
        access_secret_names = [x[1] if isinstance(x, (tuple, list)) else x for x in access_secrets]
        envvar_secrets = [x for x in access_secrets if isinstance(x, str)]
        mount_secrets = [x for x in access_secrets if isinstance(x, (tuple, list))]

        if sa is None and "service_account_email" not in service_config_kwargs:
            trans_tbl = str.maketrans(dict.fromkeys('aeiouAEIOU-_'))
            m = hashlib.sha256()
            m.update(sane_utils.name_resource(resource_name, force=True).encode())

            account_id = f"fnsa-{resource_name.translate(trans_tbl)}{m.hexdigest()}"[:28]
            display_name = f"Function SA for {project_name}/{target}/{resource_name}"

            self.sa = GoogleServiceAccount(
                f"ksa-{resource_name}",
                account_id=account_id,
                display_name=display_name,
                is_workload_iduser=False,
                access_secrets=access_secret_names,
                publish_to=publish_to,
                use_firestore=use_firestore,
                # subscribe_to=subscriptions,
                opts=pulumi.ResourceOptions(parent=self),
            )
            sa = self.sa.sa

        service_config_kwargs.setdefault("service_account_email", sa.email)

        if build_config_kwargs is None:
            build_config_kwargs = {}
        if not build_config_kwargs.get("runtime"):
            build_config_kwargs["runtime"] = "python312"

        if "entry_point" not in build_config_kwargs:
            build_config_kwargs["entry_point"] = "receiver"

        if "source" not in build_config_kwargs:
            assert source_bucket is not None, f"Source bucket is required for {resource_name}"

            source_dir = os.path.abspath(source_dir)
            code_archive = pulumi.AssetArchive({
                '.': pulumi.FileArchive(source_dir)
            })

            source_archive_object = gcp.storage.BucketObject(
                resource_name,
                bucket=source_bucket.name,
                source=code_archive
            )

            source = gcp.cloudfunctionsv2.FunctionBuildConfigSourceArgs(
                storage_source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                    bucket=source_bucket.name,
                    object=source_archive_object.name,
                ),
            )
            build_config_kwargs["source"] = source

        if subscribe_to is not None:
            assert "event_trigger" not in extra_kwargs, "You cannot specify both event_trigger and subscribe_topic"
            event_trigger = gcp.cloudfunctionsv2.FunctionEventTriggerArgs(
                event_type="google.cloud.pubsub.topic.v1.messagePublished",
                pubsub_topic=subscribe_to.id,
            )
            extra_kwargs["event_trigger"] = event_trigger

        if publish_to is None:
            publish_to = {}

        environment_variables: dict = service_config_kwargs.get("environment_variables", {})
        for _name, topic in publish_to.items():
            environment_variables[f"{_name.replace('-', '_')}_topic".upper()] = topic.id.apply(lambda _id: _id)

        if use_firestore:
            firestore_id = sane_utils.get_firestore_id()
            regex = r"projects/(?P<project_id>.*)/databases/(?P<database>.*)"
            match = re.match(regex, firestore_id)
            if match:
                dd = match.groupdict()
                environment_variables["FIRESTORE_DATABASE"] = dd['database']
                environment_variables["FIRESTORE_PROJECT_ID"] = dd['project_id']
                environment_variables["FIRESTORE_ID"] = firestore_id

        if len(envvar_secrets):
            secret_environment_variables: list = service_config_kwargs.get("secret_environment_variables", [])
            for secret in envvar_secrets:
                secret_environment_variables.append(
                    gcp.cloudfunctionsv2.FunctionServiceConfigSecretEnvironmentVariableArgs(
                        key=secret.replace("-", "_").upper(),
                        secret=sane_utils.name_resource(secret),
                        project_id=sane_utils.get_secretmanager_project_id(),
                        version=sane_utils.get_var_for_target(f"{secret}_secret_version", default="latest")
                    )
                )
            service_config_kwargs["secret_environment_variables"] = secret_environment_variables

        if len(mount_secrets):
            secret_volumes: list = service_config_kwargs.get("secret_volumes", [])
            for mount_path, secret in mount_secrets:
                secret_volumes.append(
                    FunctionServiceConfigSecretVolumeArgs(
                        mount_path=mount_path,
                        project_id=sane_utils.get_secretmanager_project_id(),
                        secret=sane_utils.name_resource(secret),
                    )
                )
                environment_variables[f'{secret.upper().replace("-", "_")}_PATH'] = "/".join((mount_path, secret))
            service_config_kwargs["secret_volumes"] = secret_volumes

        service_config_kwargs["environment_variables"] = environment_variables

        cloud_function = gcp.cloudfunctionsv2.Function(
            resource_name,
            name=sane_utils.name_resource(resource_name),
            project=project_id,
            location=region,
            build_config=gcp.cloudfunctionsv2.FunctionBuildConfigArgs(
                **build_config_kwargs,
            ),
            service_config=gcp.cloudfunctionsv2.FunctionServiceConfigArgs(
                **service_config_kwargs
            ),
            **extra_kwargs,
        )

        self.function = cloud_function
