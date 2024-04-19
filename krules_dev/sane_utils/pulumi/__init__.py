import importlib
import os
import typing

from pulumi import automation as auto, StackReference

from krules_dev.sane_utils.google import log
from .google import *
from krules_dev.sane_utils.stdvars import inject


@inject
def make_pulumi_stack_recipes(base_stack_name, project_name=None, target=None, program: typing.Callable = None,
                              configs: dict[str, str] = None, up_deps=None):
    if up_deps is None:
        up_deps = []

    stack_name = f"{base_stack_name}-{target}"

    def _get_stack():
        nonlocal target, project_name, program, stack_name

        if program is None:
            program = lambda: importlib.import_module("stack")

        if "PULUMI_CONFIG_PASSPHRASE" not in os.environ and not "PULUMI_CONFIG_PASSPHRASE_FILE" in os.environ:
            os.environ["PULUMI_CONFIG_PASSPHRASE"] = ""

        stack = auto.create_or_select_stack(
            stack_name=stack_name,
            project_name=project_name,
            program=program,
        )

        if configs is not None:
            for k, v in configs.items():
                stack.set_config(k, auto.ConfigValue(v))

        return stack

    @recipe(
        name="pulumi_up",
        info=f"Create or update stack for target {target}",
        recipe_deps=up_deps
    )
    def make_pulumi_up_recipe():
        nonlocal program

        stack = _get_stack()

        log.debug("Successfully initialized stack", target=target, project_name=project_name)

        pulumi_up_kwargs = {}
        if bool(eval(os.environ.get("PULUMI_DEBUG", "0"))):
            pulumi_up_kwargs["debug"] = True
            pulumi_up_kwargs["log_verbosity"] = 9
            pulumi_up_kwargs["log_to_std_err"] = True
            pulumi_up_kwargs["log_flow"] = True

        _ = stack.up(on_output=log.debug, **pulumi_up_kwargs)

        log.info("Stack updated")

    @recipe(
        name="pulumi_refresh",
        info=f"Refresh stack for target {target}"
    )
    def make_pulumi_refresh_recipe():
        nonlocal program

        stack = _get_stack()

        _ = stack.refresh(on_output=log.debug)

    @recipe(
        name="pulumi_destroy",
        info=f"Destroy stack for target {target}"
    )
    def make_pulumi_destroy_recipe():
        nonlocal program

        stack = _get_stack()

        _ = stack.destroy(on_output=log.debug)

        log.info("Stack destroyed")

@inject
def get_stack_reference(
        base_stack_name: str,
        project_name: str = None,
        target: str = None,
        organization=os.environ.get("PULUMI_ORGANIZATION", "organization")
) -> StackReference:
    return StackReference(
        f"{organization}/{project_name}/{base_stack_name}-{target}"
    )
