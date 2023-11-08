import json
import os
import sh

from krules_dev.sane_utils import inject, get_cmd_from_env


@inject
def get_command(stack_name: str = None,
                project_name: str = None,
                organization=os.environ.get("PULUMI_ORGANIZATION", "organization")) -> sh.Command:
    cmd = get_cmd_from_env("pulumi")
    if stack_name is not None:
        cmd = cmd.bake("--stack", f"{organization}/{project_name}/{stack_name}")
    return cmd


@inject
def get_stack_outputs(base_stack_name: str,
                      project_name: str = None,
                      target: str = None,
                      organization=os.environ.get("PULUMI_ORGANIZATION", "organization")
                      ) -> dict:

    return json.loads(
        get_command(f"{base_stack_name}-{target}", project_name=project_name, organization=organization).stack("output", "--json")
    )
