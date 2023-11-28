import pulumi
import pulumi_gcp as gcp
import sh
from pulumi_google_native.cloudresourcemanager import v1 as gcp_resourcemanager_v1


from krules_dev import sane_utils
from krules_dev.sane_utils import inject


class ArtifactRegistry(pulumi.ComponentResource):
    @inject
    def __init__(self, resource_name: str,
                 project_id: str = None,
                 cluster_project_id: str = None,
                 region: str = None,
                 format: str = "DOCKER",
                 opts: pulumi.ResourceOptions = None,
                 **kwargs) -> None:
        super().__init__('sane:gcp:ArtifactRegistry', resource_name, None, opts)

        if "project" not in kwargs:
            kwargs['project'] = project_id

        self.repository = gcp.artifactregistry.Repository(
            resource_name,
            location=region,
            repository_id=sane_utils.name_resource(sane_utils.check_env("project_name")),
            format=format,
            opts=pulumi.ResourceOptions(parent=self)
        )
        sh.Command(sane_utils.check_cmd("gcloud")).auth("configure-docker", f"{region}-docker.pkg.dev", "--quiet")

        # if the cluster is in another project, the related compute engine sa in authorized to pull images
        if cluster_project_id != project_id:
            project_number = gcp_resourcemanager_v1.get_project(project=cluster_project_id).project_number
            # bindings = gcp.organizations.get_iam_policy(
            #     bindings=[gcp.organizations.GetIAMPolicyBindingArgs(
            #         role="roles/artifactregistry.reader",
            #         members=[
            #             f"serviceAccount:{project_number}-compute@developer.gserviceaccount.com",
            #         ]
            #     )]
            # )
            #
            # self.repo_policy = gcp.artifactregistry.RepositoryIamPolicy(
            #     "repo_policy",
            #     project=self.repository.project,
            #     location=self.repository.location,
            #     repository=self.repository.name,
            #     policy_data=bindings.policy_data
            # )
            self.cluster_iam_member = gcp.artifactregistry.RepositoryIamMember(
                "repo_iam",
                project=self.repository.project,
                location=self.repository.location,
                repository=self.repository.name,
                role="roles/artifactregistry.reader",
                member=f"serviceAccount:{project_number}-compute@developer.gserviceaccount.com",
            )

        self.register_outputs({
            "repository": self.repository
        })

