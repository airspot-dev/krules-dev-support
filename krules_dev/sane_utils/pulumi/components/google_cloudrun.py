import hashlib
from typing import List, Mapping, Sequence, Any

import pulumi
import pulumi_gcp as gcp
from pulumi import Output
from pulumi_gcp.artifactregistry import Repository
from pulumi_gcp.cloudrunv2 import ServiceTemplateContainerEnvArgs, ServiceTemplateArgs, \
    ServiceTemplateContainerEnvValueSourceArgs, ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs
from pulumi_gcp.cloudrunv2.outputs import ServiceTemplateContainer
from pulumi_gcp.eventarc import TriggerMatchingCriteriaArgs, TriggerDestinationCloudRunServiceArgs
from pulumi_google_native.cloudresourcemanager import v1 as gcp_resourcemanager_v1

from krules_dev import sane_utils
from krules_dev.sane_utils import inject
from krules_dev.sane_utils.pulumi.components import SaneDockerImage, GoogleServiceAccount


class CloudRun(pulumi.ComponentResource):

    @inject
    def __init__(self, resource_name: str,
                 target: str = None,
                 project_name: str = None,
                 project_id: str = None,
                 region: str = None,
                 gcp_repository: Repository | Output[Repository] = None,
                 image_name: str = None,
                 build_args: dict = None,
                 context: str = ".build",
                 dockerfile: str = "Dockerfile",
                 sa: gcp.serviceaccount.Account | Output[gcp.serviceaccount.Account] = None,
                 require_authentication: bool = True,
                 access_secrets: List[str] = None,
                 publish_to: Mapping[str, gcp.pubsub.Topic] = None,
                 subscribe_to: Mapping[str, Mapping[str, Any]] = None,
                 use_firestore: bool = None,
                 service_kwargs: dict = None,
                 service_template_kwargs: dict = None,
                 app_container_kwargs: dict = None,
                 extra_containers: Sequence[ServiceTemplateContainer] = None,
                 opts: pulumi.ResourceOptions = None) -> None:

        super().__init__('sane:gcp:CloudRun', resource_name, None, opts)

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
        if sa is None:
            # account id must be <= 28 chars
            # we use a compressed name
            # display name is used to provide account details
            trans_tbl = str.maketrans(dict.fromkeys('aeiouAEIOU-_'))
            m = hashlib.sha256()
            m.update(sane_utils.name_resource(resource_name, force=True).encode())
            account_id = f"ksa-{resource_name.translate(trans_tbl)}{m.hexdigest()}"[:28]
            display_name = f"KSA for {project_name}/{target}/{resource_name}"

            self.sa = GoogleServiceAccount(
                f"ksa-{resource_name}",
                account_id=account_id,
                display_name=display_name,
                is_workload_iduser=False,
                access_secrets=access_secrets,
                publish_to=publish_to,
                use_firestore=use_firestore,
                # subscribe_to=subscriptions,
                opts=pulumi.ResourceOptions(parent=self),
            )
            sa = self.sa.sa

        # CREATE CLOUDRUN RESOURCE
        if service_kwargs is None:
            service_kwargs = {}
        if "project" not in service_kwargs:
            service_kwargs["project"] = project_id
        if "location" not in service_kwargs:
            service_kwargs["location"] = region
        if "ingress" not in service_kwargs:
            service_kwargs["ingress"] = "INGRESS_TRAFFIC_INTERNAL_ONLY"

        if service_template_kwargs is None:
            service_template_kwargs = {}

        if app_container_kwargs is None:
            app_container_kwargs = {}

        app_container_env = app_container_kwargs.pop("envs", [])

        app_container_env.extend([
            ServiceTemplateContainerEnvArgs(
                name="PROJECT_NAME",
                value=project_name
            ),
            ServiceTemplateContainerEnvArgs(
                name="TARGET",
                value=target,
            ),
            ServiceTemplateContainerEnvArgs(
                name="CE_SOURCE",
                value=sane_utils.get_var_for_target("ce_source", resource_name),
            ),
            ServiceTemplateContainerEnvArgs(
                name="PUBLISH_PROCEVENTS_LEVEL",
                value=sane_utils.get_var_for_target("publish_procevents_level", default="0")
            ),
            ServiceTemplateContainerEnvArgs(
                name="PUBLISH_PROCEVENTS_MATCHING",
                value=sane_utils.get_var_for_target("publish_procevents_matching", default="*")
            ),
        ])

        pysnooper_disabled = bool(eval(sane_utils.get_var_for_target("pysnooper_disabled", default="1")))
        if pysnooper_disabled:
            app_container_env.append(
                ServiceTemplateContainerEnvArgs(
                    name="PYSNOOPER_DISABLED",
                    value="1"
                )
            )


        # project_number = gcp_resourcemanager_v1.get_project(project=secretmanager_project_id).project_number
        if access_secrets is None:
            access_secrets = []
        for secret in access_secrets:
            app_container_env.append(
                ServiceTemplateContainerEnvArgs(
                    name=secret.upper(),
                    value_source=ServiceTemplateContainerEnvValueSourceArgs(
                        secret_key_ref=ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
                            secret=sane_utils.name_resource(secret),
                            version=sane_utils.get_var_for_target(f"{secret}_secret_version", default="latest")
                        )
                    )
                )
            )

        if publish_to is None:
            publish_to = {}
        for _name, topic in publish_to.items():
            app_container_env.append(
                ServiceTemplateContainerEnvArgs(
                    name=f"{_name.replace('-', '_')}_topic".upper(),
                    value=topic.id.apply(lambda _id: _id),
                )
            )

        app_container = ServiceTemplateContainer(
            image=self.image.image.repo_digest,
            name=resource_name,
            envs=app_container_env,
            **app_container_kwargs
        )

        if extra_containers is None:
            extra_containers = []

        containers = [app_container, *extra_containers]

        template_spec = ServiceTemplateArgs(
            service_account=sa.email,
            containers=containers,
            **service_template_kwargs,
        )

        service_kwargs["template"] = template_spec

        self.service = gcp.cloudrunv2.Service(
            resource_name,
            **service_kwargs
        )

        if not require_authentication:
            gcp.cloudrunv2.ServiceIamMember(
                f"{resource_name}_allUsers",
                name=self.service.name,
                project=service_kwargs.get("project"),
                location=service_kwargs.get("location"),
                role="roles/run.invoker",
                member="allUsers",
            )

        # create eventarc triggers
        project_number = gcp_resourcemanager_v1.get_project(project_id).project_number
        if subscribe_to is None:
            subscribe_to = {}
        self.triggers = []
        for _name, sub_kwargs in subscribe_to.items():
            _matching_criterias = [TriggerMatchingCriteriaArgs(**m_kwargs)
                                   for m_kwargs in sub_kwargs["matching_criterias"]]
            _event_data_content_type = sub_kwargs.get("event_data_content_type", "application/protobuf")
            _path = sub_kwargs.get("path", "/")
            trigger = gcp.eventarc.Trigger(
                _name,
                name=sane_utils.name_resource(_name),
                matching_criterias=_matching_criterias,
                event_data_content_type=_event_data_content_type,
                location=sane_utils.get_region(),
                service_account=f"{project_number}-compute@developer.gserviceaccount.com",
                destination=gcp.eventarc.TriggerDestinationArgs(
                    cloud_run_service=TriggerDestinationCloudRunServiceArgs(
                        service=self.service.name,
                        region=sane_utils.get_region(),
                        path=_path,
                    )
                ),
                opts=pulumi.ResourceOptions(parent=self),
            )
            self.triggers.append(trigger)
            # eventarc_firestore_trigger_written = gcp.eventarc.Trigger(
            #     "eventarc-firestore-trigger-written",
            #     matching_criterias=[
            #         gcp.eventarc.TriggerMatchingCriteriaArgs(
            #             attribute="type",
            #             value="google.cloud.firestore.document.v1.written",
            #         ),
            #         gcp.eventarc.TriggerMatchingCriteriaArgs(
            #             attribute="database",
            #             value=sane_utils.get_firestore_database(),
            #         ),
            #     ],
            #     event_data_content_type="application/protobuf",
            #     destination=gcp.eventarc.TriggerDestinationArgs(
            #         cloud_run_service=TriggerDestinationCloudRunServiceArgs(
            #             service=service.service.name,
            #             path="/written",
            #         ),
            #     )
            # )

        # service = gcp.cloudrunv2.Service(
        #     resource_name,
        #     ingress="INGRESS_TRAFFIC_ALL",
        #     project=project_id,
        #     location=region,
        #     template=gcp.cloudrunv2.ServiceTemplateArgs(
        #         containers=[
        #             gcp.cloudrunv2.ServiceTemplateContainerArgs(
        #                 image=image.repo_digest.apply(
        #                     lambda digest:
        #                         base_stack_ref.get_output('docker_repository').apply(
        #                             lambda repository:
        #                                 f"{repository['location']}-docker.pkg.dev/{repository['project']}/{repository['repository_id']}/{app_name}@{digest.split('@')[1]}"
        #                         )
        #                 ),
        #                 envs=[
        #                     ServiceTemplateContainerEnvArgs(
        #                         name="PUBLISH_PROCEVENTS_LEVEL",
        #                         value=sane_utils.get_var_for_target("PUBLISH_PROCEVENTS_LEVEL", default="0")
        #                     ),
        #                     ServiceTemplateContainerEnvArgs(
        #                         name="PUBLISH_PROCEVENTS_MATCHING",
        #                         value=sane_utils.get_var_for_target("PUBLISH_PROCEVENTS_MATCHING", default="*")
        #                     ),
        #                     ServiceTemplateContainerEnvArgs(
        #                         name="PROJECT_NAME",
        #                         value=project_name
        #                     ),
        #                     ServiceTemplateContainerEnvArgs(
        #                         name="PUBSUB_SINK",
        #                         value=f'{project_name}-ingestion-{target}',
        #                     ),
        #                     ServiceTemplateContainerEnvArgs(
        #                         name="PUBSUB_PROCEVENTS_SINK",
        #                         value=f'{project_name}-procevents-{target}',
        #                     ),
        #                     ServiceTemplateContainerEnvArgs(
        #                         name="TARGET",
        #                         value=target
        #                     ),
        #                     ServiceTemplateContainerEnvArgs(
        #                         name="FIRESTORE_PROJECT_ID",
        #                         value=firestore_project_id
        #                     ),
        #                     ServiceTemplateContainerEnvArgs(
        #                         name="API_KEY",
        #                         value_source=ServiceTemplateContainerEnvValueSourceArgs(
        #                             secret_key_ref=ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
        #                                 secret=f"{project_name}-ingestion_api_key-{target}",
        #                                 version="latest"
        #                             )
        #                         ),
        #                     ),
        #                     ServiceTemplateContainerEnvArgs(
        #                         name="CELERY_BROKER",
        #                         value_source=ServiceTemplateContainerEnvValueSourceArgs(
        #                             secret_key_ref=ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
        #                                 secret=f"{project_name}-celery_broker-{target}",
        #                                 version="latest"
        #                             )
        #                         ),
        #                     ),
        #                     ServiceTemplateContainerEnvArgs(
        #                         name="CELERY_RESULTS_BACKEND",
        #                         value_source=ServiceTemplateContainerEnvValueSourceArgs(
        #                             secret_key_ref=ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
        #                                 secret=f"{project_name}-celery_results_backend-{target}",
        #                                 version="latest"
        #                             )
        #                         ),
        #                     )
        #                 ],
        #             )],
        #         vpc_access=gcp.cloudrunv2.ServiceTemplateVpcAccessArgs(
        #             connector=f'projects/{vpcaccess_connector_project_id}/locations/{vpcaccess_connector_location}/connectors/{vpcaccess_connector_name}',
        #             egress="ALL_TRAFFIC",
        #         )
        #     )
        # )
