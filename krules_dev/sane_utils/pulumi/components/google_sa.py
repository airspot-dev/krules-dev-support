from collections.abc import Iterable
from typing import List, Mapping, Sequence, Tuple

import pulumi
import pulumi_gcp as gcp
from pulumi_google_native.cloudresourcemanager import v1 as gcp_resourcemanager_v1
from pulumi_kubernetes.core.v1 import ServiceAccount, ServiceAccountPatch
from pulumi_kubernetes.meta.v1 import ObjectMetaPatchArgs

from krules_dev import sane_utils
from krules_dev.sane_utils import inject, get_hashed_resource_name


class GoogleServiceAccount(pulumi.ComponentResource):

    @inject
    def __init__(self, resource_name: str,
                 account_id: str = None,
                 display_name: str = "",
                 project_id: str = None,
                 is_workload_iduser: object = False,
                 cluster_project_id: str = None,
                 namespace: str = None,
                 ksa: ServiceAccount = None,
                 access_secrets: List[str] = None,
                 publish_to: Mapping[str, gcp.pubsub.Topic] = None,
                 subscribe_to: Mapping[str, gcp.pubsub.Subscription] = None,
                 use_firestore: bool = False,
                 secretmanager_project_id: str = None,
                 opts: pulumi.ResourceOptions = None) -> None:

        super().__init__('sane:GoogleServiceAccount', resource_name, None, opts)

        if access_secrets is None:
            access_secrets = []
        if publish_to is None:
            publish_to = {}
        if subscribe_to is None:
            subscribe_to = {}

        if account_id is None:
            account_id = get_hashed_resource_name(resource_name, prefix="sa-")

        self.sa = gcp.serviceaccount.Account(
            resource_name,
            opts=pulumi.ResourceOptions(parent=self),
            project=project_id,
            account_id=account_id,
            display_name=display_name,
        )

        if is_workload_iduser:
            gcp.serviceaccount.IAMBinding(
                f"{resource_name}-bind-ksa",
                opts=pulumi.ResourceOptions(parent=self),
                service_account_id=self.sa.email.apply(
                    lambda email: f"projects/{project_id}/serviceAccounts/{email}"
                ),
                role="roles/iam.workloadIdentityUser",
                members=ksa.metadata.apply(
                    lambda metadata: [
                        f"serviceAccount:{cluster_project_id}.svc.id.goog[{namespace}/{metadata['name']}]"
                    ]
                )
            )

            ServiceAccountPatch(
                "ksa-patch",
                metadata=ObjectMetaPatchArgs(
                    name=ksa.metadata.apply(
                        lambda metadata: metadata['name']
                    ),
                    annotations={
                        "iam.gke.io/gcp-service-account": self.sa.email.apply(lambda email: email)
                    }
                ),
            )

        #project_number = gcp_resourcemanager_v1.get_project(project=secretmanager_project_id).project_number
        for secret in access_secrets:
            secret_ref = gcp.secretmanager.get_secret(
                project=secretmanager_project_id,
                secret_id=sane_utils.name_resource(secret)
            )

            res_name = f'{resource_name}_{secret}_accessor_member'
            member = gcp.secretmanager.SecretIamMember(
                res_name,
                project=secret_ref.project,
                secret_id=secret_ref.secret_id,
                role="roles/secretmanager.secretAccessor",
                member=self.sa.email.apply(
                        lambda email: f"serviceAccount:{email}"
                ),
                opts=pulumi.ResourceOptions(parent=self),
            )
            setattr(self, res_name, member)

        for _name, topic in publish_to.items():
            res_name = f'{resource_name}_{_name}_publisher'
            member = gcp.pubsub.TopicIAMMember(
                res_name,
                project=topic.project,
                topic=topic.name,
                role='roles/pubsub.publisher',
                member=self.sa.email.apply(lambda email: f"serviceAccount:{email}"),
                opts=pulumi.ResourceOptions(parent=self),
            )
            setattr(self, res_name, member)

        for _name, subscription in subscribe_to.items():
            res_name = f'{resource_name}_{_name}_subscriber'

            my_policy = gcp.organizations.get_iam_policy(
                bindings=[{
                    "role": 'roles/pubsub.subscriber',
                    "members": self.sa.email.apply(
                        lambda email: [
                            f"serviceAccount:{email}"
                        ]
                    )
                }]
            )

            policy = gcp.pubsub.SubscriptionIAMPolicy(
                res_name,
                project=subscription.project,
                subscription=subscription.name,
                policy_data=my_policy.policy_data
            )
            setattr(self, res_name, policy)

        if use_firestore:

            firestore = gcp.firestore.Database.get("firestore_iam_db", sane_utils.get_firestore_id())
            gcp.projects.IAMMember(
                "firestore_iam",
                condition=gcp.projects.IAMMemberConditionArgs(
                    title="Access firestore database",
                    expression=firestore.id.apply(
                        lambda _id: f"resource.name=='{_id}'"
                    )
                ),
                member=self.sa.email.apply(
                    lambda email: f"serviceAccount:{email}",
                ),
                project=firestore.project,
                role="roles/datastore.user",
            )

        self.register_outputs({})
