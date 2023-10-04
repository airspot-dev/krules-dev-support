import inspect
import json
import os
import sys
import re
from typing import Callable, Literal, Tuple

from google.cloud import secretmanager

import sh
import yaml
from structlog.contextvars import bind_contextvars, clear_contextvars

from krules_dev import sane_utils
from krules_dev.sane_utils.base import recipe

# logger = logging.getLogger("__sane__")

import structlog

log = structlog.get_logger()

abs_path = os.path.abspath(inspect.stack()[-1].filename)
root_dir = os.path.dirname(abs_path)


def make_enable_apis_recipe(google_apis, project_id=None, **recipe_kwargs):
    if "info" not in recipe_kwargs:
        recipe_kwargs["info"] = "Enable required Google API"

    if project_id is None:
        target, _ = sane_utils.get_targets_info()
        project_id = sane_utils.get_var_for_target("project_id", target, True)

    @recipe(**recipe_kwargs)
    def enable_google_apis():
        gcloud = sane_utils.get_cmd_from_env("gcloud").bake(project=project_id)

        log.info(f"Enabling GCP APIs, this may take several minutes...", project_id=project_id)
        for api in google_apis:
            if api.find(".") < 0:
                api = f"{api}.googleapis.com"
            log.info(f"enable API...", api=api)
            gcloud.services.enable(api)


def get_cluster_location_from_env(target: str) -> Tuple[Literal['zone', 'region'], str | None]:
    location_type: Literal['zone', 'region'] = "zone"
    region_or_zone = sane_utils.get_var_for_target("cluster_zone", target)
    if not region_or_zone:
        location_type = "region"
        region_or_zone = sane_utils.get_var_for_target("cluster_region", target)
        if not region_or_zone:
            location_type = "zone"
            region_or_zone = sane_utils.get_var_for_target("zone", target)
            if not region_or_zone:
                location_type = "region"
                region_or_zone = sane_utils.get_var_for_target("region", target)

    return location_type, region_or_zone


def make_check_gcloud_config_recipe(project_id, region, zone, **recipe_kwargs):
    @recipe(info="Check current gcloud configuration", **recipe_kwargs)
    def check_gcloud_config():
        gcloud = sane_utils.get_cmd_from_env("gcloud")

        log.debug("Checking gcloud configuration", project_id=project_id, region=region, zone=zone)

        def _get_prop_cmd(prop):
            return gcloud.config('get-value', prop).strip()
            # return run(
            #    f"gcloud config get-value {prop}", shell=True, check=True, capture_output=True
            # ).stdout.decode("utf8").strip()

        def _set_prop_cmd(prop, value):
            return gcloud.config.set(prop, value)
            # _run(f"gcloud config set {prop} {value}", check=True)

        # PROJECT
        action = "read"
        _project_id = _get_prop_cmd("core/project")

        if _project_id == '':
            _project_id = project_id
            _set_prop_cmd("core/project", project_id)
            action = "set"
        if _project_id != project_id:
            log.error("MATCH FAILED", property="core/project", configured=_project_id, received=project_id)
            # logger.error(f"code/project '{_project_id}' does not match '{project_id}'")
            sys.exit(-1)
        log.info(f"OK", project_id_=project_id, action=action)
        # REGION
        action = "read"
        _region = _get_prop_cmd("compute/region")
        if _region == '':
            _region = region
            _set_prop_cmd("compute/region", region)
            action = "set"
        if _region != region:
            log.error("MATCH FAILED", property="compute/region", configured=_region, received=region)
            sys.exit(-1)
        log.info(f"OK", region=_region, action=action)
        # ZONE
        if zone is not None:
            action = "read"
            _zone = _get_prop_cmd("compute/zone")
            if _zone == '':
                _zone = zone
                _set_prop_cmd("compute/zone", zone)
                action = "set"
            if _zone != zone:
                log.error("MATCH FAILED", property="compute/zone", configured=_zone,
                          received=zone)
                sys.exit(-1)
            log.info(f"OK", zone=_zone, action=action)


def make_set_gke_context_recipe(fmt="{project_name}_{target}", ns_fmt="{project_name}-{target}", project_name=None,
                                target=None, activate=True, **recipe_kwargs):
    if project_name is None:
        project_name = sane_utils.check_env("PROJECT_NAME")

    if target is None:
        target, _ = sane_utils.get_targets_info()

    @recipe(
        info="Set gke kubectl config contexts",
        **recipe_kwargs
    )
    def set_gke_context():
        context_name = fmt.format(project_name=project_name.lower(), target=target.lower())
        project = sane_utils.get_var_for_target("cluster_project_id", target, False)
        if not project:
            project = sane_utils.get_var_for_target("project_id", target, True)
        cluster_name = sane_utils.get_var_for_target("cluster", target, True)
        namespace = sane_utils.get_var_for_target("namespace", target)
        if namespace is None:
            namespace = ns_fmt.format(project_name=project_name.lower(), target=target.lower())

        location_arg, region_or_zone = get_cluster_location_from_env(target)

        if not region_or_zone:
            log.error("Cluster location unknown, specify region or zone", target=target,
                      cluster=cluster_name, project=project)
            sys.exit(-1)
        log.info(
            f"Setting context for cluster",
            context_name=context_name, region_or_zone=region_or_zone, cluster=cluster_name, project=project,
            namespace=namespace
        )

        gcloud = sane_utils.get_cmd_from_env("gcloud").bake("--project", project)
        kubectl = sane_utils.get_cmd_from_env("kubectl", opts=False)

        gcloud.container.clusters("get-credentials", cluster_name, f"--{location_arg}", region_or_zone, _fg=True)

        try:
            kubectl.config("delete-context", context_name)
        except sh.ErrorReturnCode:
            pass

        kubectl.config("rename-context", f"gke_{project}_{region_or_zone}_{cluster_name}", context_name)
        kubectl.config("set-context", context_name, "--namespace", namespace)

        kubectl_opts = sane_utils.get_var_for_target("kubectl_opts", target)
        if kubectl_opts is None:
            os.environ[f"{target.upper()}_KUBECTL_OPTS"] = f"--context={context_name}"

        if activate:
            kubectl.config("use-context", context_name)


def make_ensure_billing_enabled(project_id, **recipe_kwargs):
    @recipe(**recipe_kwargs)
    def check_billing():
        log.debug("Ensuring billing enabled...", project=project_id)
        gcloud = sane_utils.get_cmd_from_env("gcloud", opts=False)
        out = gcloud.beta.billing.projects.describe("krules-dev-254113", _tee=True)
        if not "billingEnabled: true" in out:
            log.error(f"You must enable billing for this project ", project=project_id)
            sys.exit(-1)
        else:
            log.debug(f"Billing enabled", project=project_id)


def make_ensure_artifact_registry_recipe(repository_name, project_id, location="europe", format="DOCKER",
                                         **recipe_kwargs):
    @recipe(**recipe_kwargs)
    def ensure_artifact_registry():

        repository_resource_name = f"projects/{project_id}/locations/{location}/repositories/{repository_name}"

        import google.auth.transport.requests
        import google.auth
        import urllib3
        creds, _ = google.auth.default()
        if creds.token is None:
            auth_req = google.auth.transport.requests.Request()
            creds.refresh(auth_req)
        parent = f"projects/{project_id}/locations/{location}"
        headers = {"X-Goog-User-Project": project_id, "Authorization": f"Bearer {creds.token}"}
        api_url = f"https://artifactregistry.googleapis.com/v1/{parent}/repositories"

        http = urllib3.PoolManager()
        log.debug("Checking repositories...")
        resp = http.request("GET", api_url, headers=headers)
        repos = json.loads(resp.data).get("repositories", [])

        for repo in repos:
            if repo["name"] == repository_resource_name:
                log.debug(f"Repository already exists", repository=repository_name)
                return
        try:
            http.request(
                "POST", f"{api_url}?repositoryId={repository_name}", headers=headers,
                body=json.dumps({"format": format})
            )
        except Exception as ex:
            log.error(f"Error creating repository", repository=repository_name, ex=str(ex))
            return
        log.info(f"Repository created", repository=repository_name)


def make_target_deploy_recipe(
        image_base: str | Callable,
        target: str,
        baselibs: list | tuple = (),
        sources: list | tuple = (),
        out_dir: str = ".build",
        context_vars: dict = None,
):
    # target, targets = sane_utils.get_targets_info()

    bind_contextvars(
        target=target
    )

    if context_vars is None:
        context_vars = {}

    project_id = sane_utils.get_var_for_target("project_id", target)
    app_name = sane_utils.check_env("APP_NAME")
    project_name = sane_utils.check_env("PROJECT_NAME")
    namespace = sane_utils.get_var_for_target("namespace", target, default=context_vars.get("namespace", "default"))

    # if extra_target_context_vars is None:
    #    extra_target_context_vars = {}

    sources_ext = []
    origins = []
    for source in sources:
        if isinstance(source, str):
            sources_ext.append(
                {
                    "origin": source,
                    "destination": f"/app/{source}"
                }
            )
            origins.append(source)
        else:
            sources_ext.append(
                {
                    "origin": source[0],
                    "destination": source[1]
                }
            )
            origins.append(source[0])
    # making changes to these files will result in a new build
    sane_utils.update_code_hash(
        globs=[
            *origins,
            *list(map(lambda x: f"{sane_utils.check_env('KRULES_PROJECT_DIR')}/base/libs/{x}/**/*.py", baselibs)),
            os.path.join(root_dir, "k8s", "*.j2"),
            os.path.join(root_dir, "*.j2"),
        ],
        out_dir=os.path.join(root_dir, out_dir),
        output_file=".code.digest"
    )

    sane_utils.make_copy_source_recipe(
        name="prepare_source_files",
        # info="Copy the source files within the designated context to prepare for the container build.",
        location=root_dir,
        src=origins,
        dst="",
        out_dir=os.path.join(root_dir, out_dir),
        hooks=["prepare_build"],
    )

    sane_utils.make_copy_source_recipe(
        name="prepare_user_baselibs",
        # info="Copy base libraries within the designated context to prepare for the container build.",
        location=os.path.join(sane_utils.check_env("KRULES_PROJECT_DIR"), "base", "libs"),
        src=baselibs,
        dst=".user-baselibs",
        out_dir=os.path.join(root_dir, out_dir),
        hooks=["prepare_build"],
    )

    sane_utils.make_render_resource_recipes(
        globs=[
            "Dockerfile.j2"
        ],
        context_vars=lambda: {
            "app_name": app_name,
            "project_name": project_name,
            "image_base": callable(image_base) and image_base() or image_base,
            "user_baselibs": baselibs,
            "project_id": project_id,
            "target": target,
            "sources": sources_ext,
            **context_vars
        },
        hooks=[
            'prepare_build'
        ]
    )

    # sane_utils.make_render_resource_recipes(
    #     globs=[
    #         "skaffold.yaml.j2"
    #     ],
    #     context_vars=lambda: {
    #         "app_name": sane_utils.check_env("APP_NAME"),
    #         # "project_id": sane_utils.get_var_for_target("project_id", target, True),
    #         "targets": [{
    #             "name": target,
    #             "project_id": project_id,
    #             "use_cloudrun": use_cloudrun,
    #             "use_cloudbuild": use_cloudbuild,
    #             "region": region,
    #             "namespace": namespace,
    #             "kubectl_opts": kubectl_opts,
    #         }],
    #         **context_vars
    #     },
    #     hooks=[
    #         'prepare_build'
    #     ]
    # )

    sane_utils.make_render_resource_recipes(
        globs=[
            "k8s/*.j2"
        ],
        context_vars={
            "project_name": project_name,
            "app_name": app_name,
            "namespace": namespace,
            "target": target,
            "project_id": project_id,
            **context_vars
        },
        hooks=[
            'prepare_build'
        ],
        out_dir=f"{out_dir}/k8s/{target}"
    )

    success_file = os.path.join(root_dir, out_dir, ".success")
    code_digest_file = os.path.join(root_dir, out_dir, ".code.digest")
    code_changed = not os.path.exists(success_file) or os.path.exists(code_digest_file) and open(
        success_file).read() != open(code_digest_file).read()

    @recipe(info="Deploy the artifact", hook_deps=["prepare_build"])
    def deploy():

        bind_contextvars(
            target=target
        )

        if not code_changed:
            log.debug("No changes detected... Skip deploy")
            return

        use_cloudrun = int(sane_utils.get_var_for_target("USE_CLOUDRUN", target, default="0"))

        region = sane_utils.get_var_for_target("region", target, default=None)
        if use_cloudrun:
            log.debug("using CloudRun to deploy")
            if region is None:
                log.error("You must specify a region if using CloudRun")
                sys.exit(-1)
        else:
            log.debug("using Kubernetes to deploy")

        use_cloudbuild = int(sane_utils.get_var_for_target("USE_CLOUDBUILD", target, default="0"))
        if use_cloudbuild:
            log.debug("using Google Cloud Build"),

        use_buildkit = int(sane_utils.get_var_for_target("USE_BUILDKIT", target, default="0"))
        if use_buildkit:
            log.debug("using BuildKit")

        use_dockercli = int(sane_utils.get_var_for_target("USE_DOCKERCLI", target, default="0"))
        if use_buildkit:
            log.debug("using Docker CLI")

        build_platform = sane_utils.get_var_for_target("build_platform", target, default="linux/amd64")

        kubectl_opts = sane_utils.get_var_for_target("kubectl_opts", target, default=None)
        if kubectl_opts:
            kubectl_opts = re.split(" ", kubectl_opts)
        else:
            kubectl_opts = []

        kubectl_ctx = sane_utils.get_var_for_target("kubectl_ctx", target, default=None)
        if kubectl_ctx is None and not use_cloudrun:
            kubectl_ctx = f"gke_{project_name}_{target}"
            log.debug(f"KUBECTL_CTX not specified for target, using {kubectl_ctx}")
        # try:
        #     sane_utils.get_cmd_from_env("kubectl").config("use-context", kubectl_ctx, _fg=True)
        # except sh.ErrorReturnCode:
        #     log.error("cannot set kubectl context", context=kubectl_ctx)
        #     sys.exit(-1)

        repo_name = sane_utils.get_var_for_target("DOCKER_REGISTRY", target)
        log.debug("Get DOCKER_REGISTRY from env", value=repo_name)
        if repo_name is None:
            if region is None:
                region = sane_utils.get_var_for_target("cluster_region", target, True)
                if region is None:
                    log.error(
                        "you need to provide a REGION where your artifact registry resides, or provide a value for DOCKER_REGISTRY")
                    sys.exit(-1)
            artifact_registry = f"{sane_utils.check_env('PROJECT_NAME')}-{target}"
            repo_name = f"{region}-docker.pkg.dev/{project_id}/{artifact_registry}"
            log.debug("Using project artifact registry", value=repo_name)

        with sane_utils.pushd(os.path.join(root_dir, out_dir)):
            skaffold = sh.Command(
                sane_utils.check_cmd("skaffold")
            )

            skaffold_config = {
                "apiVersion": "skaffold/v3alpha1",
                "kind": "Config",
                "profiles": [{
                    "name": target,
                    "manifests": {
                        "rawYaml": [
                            f"k8s/{target}/*.yaml"
                        ]
                    },
                    "build": {
                        "artifacts": [{
                            "image": app_name,
                        }]
                    },
                    "deploy": {}
                }]
            }

            if use_cloudbuild:
                skaffold_config["profiles"][0]["build"]["googleCloudBuild"] = {
                    "projectId": project_id
                }
            else:
                skaffold_config["profiles"][0]["build"]["local"] = {
                    "useDockerCLI": bool(use_dockercli),
                    "useBuildkit": bool(use_buildkit)
                }

            if use_cloudrun:
                skaffold_config["profiles"][0]["deploy"]["cloudrun"] = {
                    "projectid": project_id,
                    "region": region,
                }
            else:
                skaffold_config["profiles"][0]["deploy"]["kubeContext"] = kubectl_ctx
                skaffold_config["profiles"][0]["deploy"]["kubectl"] = {
                    "defaultNamespace": namespace
                }
                if len(kubectl_opts):
                    skaffold_config["profiles"][0]["deploy"]["kubectl"]["flags"] = {
                        "global": kubectl_opts
                    }

            log.debug("Running skaffold")
            with open("skaffold.yaml", "w") as f:
                dump = yaml.dump(skaffold_config)
                log.debug(f"\n{dump}")
                f.write(dump)
            skaffold.run(
                default_repo=repo_name,
                profile=target,
                platform=build_platform,
                _fg=True,
            )
            log.info("Deployed")


def make_ensure_gcs_bucket_recipe(bucket_name, project_id, location="EU", **recipe_kwargs):
    @recipe(**recipe_kwargs)
    def ensure_gcs_bucket():
        gsutil = sh.Command(
            sane_utils.check_cmd(os.environ.get("GSUTIL_CMD", "gsutil"))
        )
        _bucket_name = bucket_name
        if not _bucket_name.startswith("gs://"):
            _bucket_name = f"gs://{_bucket_name}"
        bind_contextvars(
            bucket=_bucket_name, project=project_id, location=location
        )
        log.debug(f"Try to create gcs bucket", )
        # out = io.StringIO()
        # logging.getLogger('sh').setLevel(logging.DEBUG)
        # def _custom_log(ran, call_args, pid=None):
        #    log.debug("_>", ran=ran, pid=pid)
        try:
            gsutil.mb(
                "-l", location, "-p", project_id, _bucket_name,
                # _log_msg=_custom_log
            )
            log.info("gcs bucket created")
        except Exception as ex:
            log.debug("the bucket has not been created (maybe it already exists)", exit_code=ex.exit_code)

        clear_contextvars()
        # ret_code = _run(
        #     f"{gsutil} mb -l {location} -p {project_id} gs://{bucket_name}",
        #     check=False,
        #     err_to_stdout=True,
        #     errors_log_level=logging.DEBUG
        # )
        # if ret_code == 1:
        #     log.debug("the bucket has not been created (maybe it already exists)", retcode=ret_code)


def get_google_secret(base_name, version=None, fmt="{project_name}-{base_name}-{target}", project_id=None, project_name=None, target=None) -> bytes:
    if project_id is None:
        project_id = sane_utils.get_var_for_target("project_id")
    if project_name is None:
        project_name = sane_utils.check_env("project_name")
    if target is None:
        target, _ = sane_utils.get_targets_info()

    name = fmt.format(
        project_name=project_name.lower(),
        base_name=base_name,
        target=target.lower(),
    )

    if version is None:
        version = os.environ.get(f"{base_name.replace('-', '_').upper()}_SECRET_VERSION", "latest")

    client = secretmanager.SecretManagerServiceClient()
    full_name = client.secret_version_path(project_id, name, version)
    response = client.access_secret_version(name=full_name)
    return response.payload.data


