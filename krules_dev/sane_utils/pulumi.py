import importlib
import json
import os
import re
import typing

from pulumi import automation as auto
from sane import recipe

from krules_dev import sane_utils
from krules_dev.sane_utils.google import log


def make_pulumi_stack_recipes(stack_name, project_name=None, target=None, program: typing.Callable = None,
                              configs: dict[str, str] = None):
    if target is None:
        target, _ = sane_utils.get_targets_info()

    if project_name is None:
        project_name = sane_utils.check_env("project_name")

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
        info=f"Create or update stack for target {target}"
    )
    def make_pulumi_up_recipe():
        nonlocal program

        stack = _get_stack()

        log.debug("Successfully initialized stack", target=target, project_name=project_name)

        _ = stack.up(on_output=log.debug)

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
