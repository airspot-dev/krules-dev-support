import inspect
import json
import logging
import os
import sys
import re
from typing import Callable

# from subprocess import run, CalledProcessError

import sh
from structlog.contextvars import bind_contextvars, clear_contextvars

from krules_dev import sane_utils
from .base import recipe

# logger = logging.getLogger("__sane__")

import structlog

log = structlog.get_logger()

abs_path = os.path.abspath(inspect.stack()[-1].filename)
root_dir = os.path.dirname(abs_path)


def make_enable_apis_recipe(google_apis, project_id, **recipe_kwargs):
    @recipe(**recipe_kwargs)
    def enable_google_apis():
        gcloud = sane_utils.get_cmd_from_env("gcloud").bake(project=project_id)

        log.debug(f"Enabling GCP APIs, this may take several minutes...", project_id=project_id)
        for api in google_apis:
            log.debug(f"enable API...", api=api)
            gcloud.services.enable(api)


def make_check_gcloud_config_recipe(project_id, region, zone, **recipe_kwargs):
    @recipe(info="Check current gcloud configuration", **recipe_kwargs)
    def check_gcloud_config():
        gcloud = sane_utils.get_cmd_from_env("gcloud").bake(project=project_id)

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


def make_set_gke_contexts_recipe(project_name, targets, **recipe_kwargs):
    @recipe(
        info="Set gke kubectl config contexts",
        **recipe_kwargs
    )
    def set_gke_contexts():
        for idx, target in enumerate(targets):
            context_name = f"gke_{project_name}_{target.lower()}"
            project = sane_utils.get_var_for_target("cluster_project_id", target, False)
            if not project:
                project = sane_utils.get_var_for_target("project_id", target, True),
            cluster_name = sane_utils.get_var_for_target("cluster", target, True)
            namespace = sane_utils.get_var_for_target("namespace", target)
            if namespace is None:
                namespace = "default"
            region_or_zone = sane_utils.get_var_for_target("zone", target)
            location_arg = "--zone"
            if region_or_zone is None:
                region_or_zone = sane_utils.get_var_for_target("region", target, True)
                location_arg = "--region"
            log.info(
                f"Setting context for cluster",
                context=context_name, region_or_zone=region_or_zone, cluster=cluster_name, project=project,
                namespace=namespace
            )

            gcloud = sane_utils.get_cmd_from_env("gcloud")
            kubectl = sane_utils.get_cmd_from_env("kubectl", opts=False)

            gcloud.container.clusters("get-credentials", cluster_name, location_arg, region_or_zone, _fg=True)

            try:
                kubectl.config("delete-context", context_name)
            except sh.ErrorReturnCode:
                pass

            kubectl.config("rename-context", f"gke_{project}_{region_or_zone}_{cluster_name}", context_name)
            kubectl.config("set-context", context_name, "--namespace", namespace)

            kubectl_opts = sane_utils.get_var_for_target("kubectl_opts", target)
            if kubectl_opts is None:
                os.environ[f"{target.upper()}_KUBECTL_OPTS"] = f"--context={context_name}"

            if idx == 0:
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

    use_cloudrun = int(sane_utils.get_var_for_target("USE_CLOUDRUN", target, default="0"))
    if use_cloudrun:
        log.debug("using CloudRun to deploy")
    else:
        log.debug("using Kubernetes to deploy")

    use_cloudbuild = int(sane_utils.get_var_for_target("USE_CLOUDBUILD", target, default="0"))
    if use_cloudbuild:
        log.debug("using Google Cloud Build"),

    if context_vars is None:
        context_vars = {}
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
        info="Copy the source files within the designated context to prepare for the container build.",
        location=root_dir,
        src=origins,
        dst="",
        out_dir=os.path.join(root_dir, out_dir),
        hooks=["prepare_build"],
    )

    sane_utils.make_copy_source_recipe(
        name="prepare_user_baselibs",
        info="Copy base libraries within the designated context to prepare for the container build.",
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
            "app_name": sane_utils.check_env("APP_NAME"),
            "project_name": sane_utils.check_env("PROJECT_NAME"),
            "image_base": callable(image_base) and image_base() or image_base,
            "user_baselibs": baselibs,
            "project_id": sane_utils.get_var_for_target("project_id", target, True),
            "target": target,
            "sources": sources_ext,
            **context_vars
        },
        hooks=[
            'prepare_build'
        ]
    )

    project_id = sane_utils.get_var_for_target("project_id", target)
    use_cloudrun = int(sane_utils.get_var_for_target("use_cloudrun", target, default="0"))
    use_cloudbuild = int(sane_utils.get_var_for_target("use_cloudbuild", target, default="0"))
    region = sane_utils.get_var_for_target("region", target, default=None)
    if use_cloudrun and region is None:
        log.error("You must specify a region if using CloudRun")
        sys.exit(-1)
    namespace = sane_utils.get_var_for_target("namespace", target, default="default")
    kubectl_opts = sane_utils.get_var_for_target("kubectl_opts", target, default=None)
    if kubectl_opts:
        kubectl_opts = re.split(" ", kubectl_opts)
    else:
        kubectl_opts = []

    sane_utils.make_render_resource_recipes(
        globs=[
            "skaffold.yaml.j2"
        ],
        context_vars=lambda: {
            "app_name": sane_utils.check_env("APP_NAME"),
            # "project_id": sane_utils.get_var_for_target("project_id", target, True),
            "targets": [{
                "name": target,
                "project_id": project_id,
                "use_cloudrun": use_cloudrun,
                "use_cloudbuild": use_cloudbuild,
                "region": region,
                "namespace": namespace,
                "kubectl_opts": kubectl_opts,
            }],
            **context_vars
        },
        hooks=[
            'prepare_build'
        ]
    )

    sane_utils.make_render_resource_recipes(
        globs=[
            "k8s/*.j2"
        ],
        context_vars={
            "project_name": sane_utils.check_env("PROJECT_NAME"),
            "app_name": sane_utils.check_env("APP_NAME"),
            "namespace": sane_utils.get_var_for_target("namespace", target, default="default"),
            "target": target,
            "project_id": sane_utils.get_var_for_target("project_id", target, True),
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

        repo_name = sane_utils.get_var_for_target("DOCKER_REGISTRY", target)
        log.debug("Get DOCKER_REGISTRY from env", value=repo_name)
        if repo_name is None:
            artifact_registry = sane_utils.check_env('PROJECT_NAME')
            region = sane_utils.get_var_for_target('region', target)
            project = sane_utils.get_var_for_target('project_id', target)
            repo_name = f"{region}-docker.pkg.dev/{project}/{artifact_registry}"
            log.debug("Using project artifact registry", value=repo_name)
        with sane_utils.pushd(os.path.join(root_dir, out_dir)):
            skaffold = sh.Command(
                sane_utils.check_cmd("skaffold")
            )

            log.debug("Running skaffold")
            skaffold.run(
                default_repo=repo_name,
                profile=target,
            )
            log.info("Deployed")


def make_ensure_gcs_bucket_recipe(bucket_name, project_id, location="EU", **recipe_kwargs):
    @recipe(**recipe_kwargs)
    def ensure_gcs_bucket():
        gsutil = sh.Command(
            sane_utils.check_cmd(os.environ.get("GSUTIL_CMD", "gsutil"))
        )
        bind_contextvars(
            bucket=bucket_name, project=project_id, location=location
        )
        log.debug(f"Try to create gcs bucket", )
        # out = io.StringIO()
        # logging.getLogger('sh').setLevel(logging.DEBUG)
        # def _custom_log(ran, call_args, pid=None):
        #    log.debug("_>", ran=ran, pid=pid)

        try:
            gsutil.mb(
                "-l", location, "-p", project_id, f"gs://{bucket_name}",
                # _log_msg=_custom_log
                # _out=out, _err=out
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
