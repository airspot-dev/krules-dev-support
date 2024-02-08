import os
import re
from typing import List, Mapping, Sequence, Tuple, Any

import pulumi
import pulumi_gcp as gcp
import pulumi_kubernetes as kubernetes
from pulumi import Output
from pulumi_gcp.artifactregistry import Repository
from pulumi_kubernetes.core.v1 import ServiceAccount, EnvVarArgs, ServiceSpecType, PersistentVolumeClaimSpecArgs, \
    ResourceRequirementsArgs

from krules_dev import sane_utils
from krules_dev.sane_utils import inject, get_hashed_resource_name
from krules_dev.sane_utils.consts import PUBSUB_PULL_CE_SUBSCRIBER_IMAGE
from krules_dev.sane_utils.pulumi.components import SaneDockerImage, GoogleServiceAccount


class GkeDeployment(pulumi.ComponentResource):

    @inject
    def __init__(self, resource_name: str,
                 target: str = None,
                 project_name: str = None,
                 project_id: str = None,
                 cluster_project_id: str = None,
                 namespace: str = None,
                 gcp_repository: Repository | Output[Repository] = None,
                 image_name: str = None,
                 build_args: dict = None,
                 context: str = ".build",
                 dockerfile: str = "Dockerfile",
                 ksa: ServiceAccount | Output[ServiceAccount] = None,
                 access_secrets: List[str] = None,
                 publish_to: Mapping[str, gcp.pubsub.Topic] = None,
                 subscribe_to: Sequence[Tuple[str, Mapping[str, Any]]] = None,
                 use_firestore: bool = False,
                 secretmanager_project_id: str = None,
                 deployment_spec_kwargs: dict = None,
                 ce_target_port: int = 8080,
                 ce_target_path: str = "/",
                 service_type: str | ServiceSpecType = None,
                 service_spec_kwargs: dict = None,
                 app_container_kwargs: dict = None,
                 app_container_pod_spec_kwargs: dict = None,
                 app_container_pvc_mounts: dict = None,
                 extra_containers: Sequence[kubernetes.core.v1.ContainerArgs] = None,
                 opts: pulumi.ResourceOptions = None) -> None:

        super().__init__('sane:gke:Deployment', resource_name, None, opts)

        # BUILD AND PUSH IMAGE
        self.image = SaneDockerImage(
            resource_name,
            gcp_repository=gcp_repository,
            image_name=image_name,
            args=build_args,
            context=context,
            dockerfile=dockerfile,
        )

        # CREATE SERVICE ACCOUNT (if None)
        if ksa is None:
            ksa = kubernetes.core.v1.ServiceAccount(
                resource_name,
            )

        # account id must be <= 28 chars
        # we use a compressed name
        # display name is used to provide account details
        account_id = get_hashed_resource_name(resource_name, prefix="ksa-")
        display_name = f"KSA for {project_name}/{target}/{resource_name}"

        # create subscriptions
        if subscribe_to is None:
            subscribe_to = []
        subscriptions = {}
        for _name, sub_kwargs in subscribe_to:
            res_name = f"sub-{resource_name}-{_name}"
            sub = gcp.pubsub.Subscription(
                res_name,
                opts=pulumi.ResourceOptions(parent=self),
                **sub_kwargs,
            )
            setattr(self, res_name, sub)

            subscriptions[_name] = sub

        env_var_secrets = []
        google_secrets = []
        if access_secrets is None:
            access_secrets = []
        for secret in access_secrets:
            from_env = sane_utils.get_var_for_target(secret)
            if from_env is None:
                google_secrets.append(secret),
            else:
                env_var_secrets.append(
                    EnvVarArgs(
                        name=secret.upper(),
                        value=from_env
                    )
                )
        self.sa = GoogleServiceAccount(
            f"ksa-{resource_name}",
            account_id=account_id,
            display_name=display_name,
            is_workload_iduser=True,
            ksa=ksa,
            namespace=namespace,
            access_secrets=google_secrets,
            publish_to=publish_to,
            subscribe_to=subscriptions,
            use_firestore=use_firestore,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # PVC
        if app_container_pvc_mounts is None:
            app_container_pvc_mounts = {}
        for _name, _data in app_container_pvc_mounts.items():
            kubernetes.core.v1.PersistentVolumeClaim(
                f"{_name}-pvc",
                metadata=kubernetes.meta.v1.ObjectMetaArgs(
                    name=_name,
                    namespace=namespace,
                    labels=dict(
                        project_name=project_name,
                        target=target,
                        resource=resource_name
                    )
                ),
                spec=PersistentVolumeClaimSpecArgs(
                    access_modes=_data.get("access_modes", ["ReadWriteOnce"]),
                    storage_class_name=_data.get("storage_class_name", "standard-rwo"),
                    resources=ResourceRequirementsArgs(
                        requests=_data.get("requests", {
                            "storage": _data.get("storage", "10Gi")
                        })
                    )
                )
            )

        # CREATE DEPLOYMENT RESOURCE
        containers = []

        app_container_env = [
            EnvVarArgs(
                name="PROJECT_NAME",
                value=project_name
            ),
            EnvVarArgs(
                name="PROJECT_ID",
                value=project_id
            ),
            EnvVarArgs(
                name="CLUSTER_PROJECT_ID",
                value=cluster_project_id,
            ),
            EnvVarArgs(
                name="TARGET",
                value=target,
            ),
            EnvVarArgs(
                name="CE_SOURCE",
                value=sane_utils.get_var_for_target("ce_source", default=resource_name),
            ),
            EnvVarArgs(
                name="PUBLISH_PROCEVENTS_LEVEL",
                value=sane_utils.get_var_for_target("publish_procevents_level", default="0")
            ),
            EnvVarArgs(
                name="PUBLISH_PROCEVENTS_MATCHING",
                value=sane_utils.get_var_for_target("publish_procevents_matching", default="*")
            ),
        ]

        pysnooper_disabled = bool(eval(sane_utils.get_var_for_target("pysnooper_disabled", default="1")))
        if pysnooper_disabled:
            app_container_env.append(
                EnvVarArgs(
                    name="PYSNOOPER_DISABLED",
                    value="1"
                )
            )

        app_container_env.extend(env_var_secrets)

        if use_firestore:
            firestore_id = sane_utils.get_firestore_id()
            regex = r"projects/(?P<project_id>.*)/databases/(?P<database>.*)"
            match = re.match(regex, firestore_id)
            if match:
                dd = match.groupdict()
                app_container_env.extend([
                    EnvVarArgs(
                        name="FIRESTORE_DATABASE",
                        value=dd['database'],
                    ),
                    EnvVarArgs(
                        name="FIRESTORE_PROJECT_ID",
                        value=dd['project_id']
                    ),
                    EnvVarArgs(
                        name="FIRESTORE_ID",
                        value=firestore_id,
                    )
                ])

        # project_number = gcp_resourcemanager_v1.get_project(project=secretmanager_project_id).project_number
        if access_secrets is None:
            access_secrets = []
        for secret in google_secrets:
            # secret_ref = gcp.secretmanager.get_secret(
            #     project=secretmanager_project_id,
            #     secret_id=sane_utils.name_resource(secret)
            # )
            secret_path = sane_utils.get_var_for_target(f"{secret}_secret_path")
            if secret_path is None:
                secret_path = "projects/{project}/secrets/{secret}/versions/{secret_version}".format(
                    # project=project_number,
                    project=secretmanager_project_id,
                    secret=sane_utils.name_resource(secret),
                    secret_version=sane_utils.get_var_for_target(f"{secret}_secret_version", default="latest"),
                )
            app_container_env.append(
                EnvVarArgs(
                    name=f"{secret.upper()}_SECRET_PATH",
                    value=secret_path
                )
            )

        if publish_to is None:
            publish_to = {}

        for k_topic, topic in publish_to.items():
            app_container_env.append(
                EnvVarArgs(
                    #name=topic.name.apply(lambda _name: f"{_name.replace('-', '_')}_topic".upper()),
                    name=f"{k_topic.replace('-', '_')}_topic".upper(),
                    value=topic.id.apply(lambda _id: _id),
                )
            )

        if app_container_kwargs is None:
            app_container_kwargs = {}
        if "env" in app_container_kwargs:
            app_container_env.extend(app_container_kwargs.pop("env"))

        if app_container_pod_spec_kwargs is None:
            app_container_pod_spec_kwargs = {}

        # PVC
        volumes = app_container_pod_spec_kwargs.get("volumes", [])
        volume_mounts = app_container_kwargs.pop("volume_mounts", [])
        for _name, _data in app_container_pvc_mounts.items():
            volumes.append(
                kubernetes.core.v1.VolumeArgs(
                    name=_name,
                    persistent_volume_claim=kubernetes.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                        claim_name=_name,
                    )
                )
            )
            volume_mounts.append(
                kubernetes.core.v1.VolumeMountArgs(
                    name=_name,
                    mount_path=_data["mount_path"]
                )
            )

        if len(volumes):
            app_container_pod_spec_kwargs["volumes"] = volumes

        if len(volume_mounts):
            app_container_kwargs["volume_mounts"] = volume_mounts


        app_container = kubernetes.core.v1.ContainerArgs(
            image=self.image.repo_digest,
            name=resource_name,
            env=app_container_env,
            **app_container_kwargs,
        )

        containers.append(app_container)

        if extra_containers is not None:
            containers.extend(extra_containers)

        # pubsub subscriptions sidecars
        pull_ce_image = os.environ.get("PUBSUB_PULL_CE_SUBSCRIBER_IMAGE", PUBSUB_PULL_CE_SUBSCRIBER_IMAGE)
        for _name, subscripton in subscriptions.items():
            subscription_env = [
                EnvVarArgs(
                    name="SUBSCRIPTION",
                    value=subscripton.id
                ),
                EnvVarArgs(
                    name="CE_SINK",
                    value=f"http://localhost:{ce_target_port}{ce_target_path}"
                ),
            ]
            if bool(eval(sane_utils.get_var_for_target("debug_subscriptions", default="0"))):
                subscription_env.append(
                    EnvVarArgs(
                        name="DEBUG",
                        value="1"
                    )
                )
            containers.append(
                kubernetes.core.v1.ContainerArgs(
                    image=pull_ce_image,
                    name=_name,
                    env=subscription_env
                )
            )

        if deployment_spec_kwargs is None:
            deployment_spec_kwargs = {}
        self.deployment = kubernetes.apps.v1.Deployment(
            f"{resource_name}_deployment",
            metadata=kubernetes.meta.v1.ObjectMetaArgs(
                name=resource_name,
                namespace=namespace,
                labels={
                    "krules.dev/app": resource_name,
                },
            ),
            spec=kubernetes.apps.v1.DeploymentSpecArgs(
                replicas=int(sane_utils.get_var_for_target(
                    f"{resource_name}_replicas", default="1")),
                selector=kubernetes.meta.v1.LabelSelectorArgs(
                    match_labels={
                        "krules.dev/app": resource_name,
                    },
                ),
                template=kubernetes.core.v1.PodTemplateSpecArgs(
                    metadata=kubernetes.meta.v1.ObjectMetaArgs(
                        labels={
                            "krules.dev/app": resource_name,
                        },
                        annotations={
                            "kubectl.kubernetes.io/default-container": resource_name,
                        },
                    ),
                    spec=kubernetes.core.v1.PodSpecArgs(
                        service_account=ksa.metadata.apply(
                            lambda metadata: metadata.get("name")
                        ),
                        containers=containers,
                        **app_container_pod_spec_kwargs,
                    ),
                ),
                **deployment_spec_kwargs,
            ),
        )

        # create service
        if service_type is not None or service_spec_kwargs is not None:
            if service_spec_kwargs is None:
                service_spec_kwargs = {}
            if service_type is not None:
                service_spec_kwargs["type"] = service_type

            if "ports" not in service_spec_kwargs:
                service_spec_kwargs["ports"] = [
                    kubernetes.core.v1.ServicePortArgs(
                        port=80,
                        protocol="TCP",
                        target_port=ce_target_port
                    )
                ]
            if "selector" not in service_spec_kwargs:
                service_spec_kwargs["selector"] = {
                    "krules.dev/app": resource_name,
                }

            self.service = kubernetes.core.v1.Service(
                f"{resource_name}_service",
                metadata=kubernetes.meta.v1.ObjectMetaArgs(
                    name=resource_name
                ),
                spec=kubernetes.core.v1.ServiceSpecArgs(
                    **service_spec_kwargs,
                ),
            )

        self.register_outputs({})

