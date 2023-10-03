import inspect
import os

import pulumi
from pulumi import Output

from krules_dev import sane_utils


class DockerImageBuilder(pulumi.ComponentResource):
    def __init__(self, name: str,
                 image_name: str | Output[str],
                 opts: pulumi.ResourceOptions = None,
                 use_gcp_build: bool = None,
                 gcp_project_id: str = None,
                 args: dict = None,
                 context: str = ".build",
                 dockerfile: str = "Dockerfile",
                 skip_push: bool = False,
                 ):
        super().__init__('sane:DockerImageBuilder', name, None, opts)
        self.name = name
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
