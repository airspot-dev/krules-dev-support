import os

import pulumi
import pulumi_docker as docker
from pulumi import Output
from pulumi_gcp.artifactregistry import Repository

from krules_dev import sane_utils
from krules_dev.sane_utils.stdvars import inject

class DockerImageBuilder(pulumi.ComponentResource):
    def __init__(self, resource_name: str,
                 image_name: str | Output[str],
                 opts: pulumi.ResourceOptions = None,
                 use_gcp_build: bool = None,
                 gcp_project_id: str = None,
                 args: dict = None,
                 context: str = ".build",
                 dockerfile: str = "Dockerfile",
                 skip_push: bool = False,
                 ):
        super().__init__('sane:DockerImageBuilder', resource_name, None, opts)
        self.name = resource_name
        self.image_name = image_name
        if use_gcp_build is None:
            use_gcp_build = bool(int(sane_utils.get_var_for_target("use_cloudbuild", default="0")))
        self.use_gcp_build = use_gcp_build
        if gcp_project_id is None:
            gcp_project_id = sane_utils.get_var_for_target("project_id")
        self.gcp_project_id = gcp_project_id
        if args is None:
            args = {}
        self.platform = sane_utils.get_var_for_target("BUILD_PLATFORM", default="linux/amd64")
        self.args = args
        context = os.path.abspath(context)
        self.context = context
        if not os.path.isabs(dockerfile):
            dockerfile = os.path.join(context, dockerfile)
        self.dockerfile = dockerfile
        self.skip_push = skip_push

    def build(self):
        if self.use_gcp_build:
            image = self.build_with_gcp()
        else:
            image = self.build_with_docker()

        return image

    def build_with_docker(self):
        import pulumi_docker as docker

        # https://www.pulumi.com/registry/packages/docker/api-docs/image/

        return docker.Image(
            self.name,
            build=docker.DockerBuildArgs(
                args=self.args,
                context=self.context,
                dockerfile=self.dockerfile,
                platform=self.platform,
            ),
            image_name=self.image_name,
            skip_push=self.skip_push
        )

    def build_with_gcp(self):
        raise NotImplementedError("CloudBuild is not YET supported")

def _debug_r(r):
    from pprint import pprint
    print(f"***************************************************")
    pprint(r)
    ff=f"{r['location']}-docker.pkg.dev/{r['project']}/{r['repository_id']}/ruleset-base"
    print(f"***************************************************")
    return ff

class SaneDockerImage(pulumi.ComponentResource):

    @inject
    def __init__(
            self, resource_name: str,
            gcp_repository: dict | Output[dict] = None,
            image_name: str = None,
            args: dict = None,
            context: str = ".build",
            dockerfile: str = "Dockerfile",
            skip_push: bool = False,
            opts: pulumi.ResourceOptions = None,
    ) -> None:
        super().__init__('sane:SaneDockerImage', resource_name, None, opts)

        if args is None:
            args = {}
        self.platform = sane_utils.get_var_for_target("BUILD_PLATFORM", default="linux/amd64")
        self.args = args
        context = os.path.abspath(context)
        self.context = context
        if not os.path.isabs(dockerfile):
            dockerfile = os.path.join(context, dockerfile)
        self.dockerfile = dockerfile
        self.skip_push = skip_push

        if gcp_repository is not None:
            if image_name is None:
                image_name = resource_name
            self.image = docker.Image(
                resource_name,
                build=docker.DockerBuildArgs(
                    args=self.args,
                    context=self.context,
                    dockerfile=self.dockerfile,
                    platform=self.platform,
                ),
                skip_push=skip_push,
                # image_name=OutputProxy(
                #     gcp_repository,
                #     "location", "project", "repository_id"
                # ).apply(
                #     lambda args: f"{args[0]}-docker.pkg.dev/{args[1]}/{args[2]}/{image_name}"
                # )
                image_name=pulumi.Output.all(
                    gcp_repository.location,
                    gcp_repository.project,
                    gcp_repository.repository_id
                ).apply(
                    lambda args: f"{args[0]}-docker.pkg.dev/{args[1]}/{args[2]}/{image_name}"
                )
                #image_name=gcp_repository.apply(
                #    lambda r: _debug_r(r)
                       # f"{r['location']}-docker.pkg.dev/{r['project']}/{r['repository_id']}/{image_name}"
                )
                # image_name=pulumi.Output.all(
                #     location=gcp_repository.location,
                #     project=gcp_repository.project,
                #     repository_id=gcp_repository.repository_id
                # ).apply(
                #     lambda args:
                #         f"{args['location']}-docker.pkg.dev/{args['project']}/{args['repository_id']}/{image_name}"
                # )

        else:
            self.image = docker.Image(
                resource_name,
                build=docker.DockerBuildArgs(
                    args=self.args,
                    context=self.context,
                    dockerfile=self.dockerfile,
                    platform=self.platform,
                ),
                skip_push=skip_push,
                image_name=image_name
            )

        self.repo_digest = pulumi.Output.all(
            self.image.image_name,
            self.image.repo_digest,
        ).apply(
            lambda args: f"{args[0]}@{args[1].split('@')[1]}"
        )

        self.register_outputs({})
