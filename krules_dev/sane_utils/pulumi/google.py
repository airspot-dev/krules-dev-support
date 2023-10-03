import os

import structlog
#from krules_dev import sane_utils
from sane import recipe

log = structlog.get_logger()



def make_init_pulumi_gcs_recipes(project_id=None, project_name=None, bucket_name=None, bucket_location=None, **recipe_kwargs):
    from krules_dev import sane_utils

    if project_id is None:
        target, _ = sane_utils.get_targets_info()
        project_id = sane_utils.get_var_for_target("project_id", target, True)

    if project_name is None:
        project_name = sane_utils.check_env("project_name")

    if bucket_name is None:
        bucket_name = os.environ.get("PULUMI_GGS_BACKEND")
        if bucket_name is None:
            bucket_name = f"gs://{project_id}-{project_name}-pulumi_state"
    if bucket_location is None:
        bucket_location = os.environ.get("PULUMI_GGS_BACKEND", "EU")
    sane_utils.google.make_ensure_gcs_bucket_recipe(
        name="ensure_palumi_state_gcs_bucket",
        info=f"Ensure Pulumi GCS bucket: {bucket_name}",
        project_id=project_id,
        bucket_name=bucket_name,
        location=bucket_location,
    )
    @recipe(
        name="pulumi_gcs_login",
        info="Pulumi GCS login",
        recipe_deps=[
            "ensure_palumi_state_gcs_bucket"
        ],
        **recipe_kwargs
    )
    def pulumi_gcs_login_recipe():
        sane_utils.get_cmd_from_env("pulumi").login(bucket_name, _fg=True)

