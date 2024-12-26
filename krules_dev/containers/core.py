
from dependency_injector import containers, providers

import pulumi
import re

from pulumi import StackReference

pulumi_config = pulumi.Config()


def get_base_stack():
    base_stack = pulumi_config.require("base_stack")
    pattern = r"^(?:([^/]+)/)?(?:([^/]+)/)?([^/]+)$"
    match = re.match(pattern, base_stack)
    if match:
        org, project, stack = match.groups()
        if org is None:
            org = "organization"
        if project is None:
            project = pulumi.get_project()
        if stack is None:
            raise ValueError("stack is required")
        return StackReference(f"{org}/{project}/{stack}")
    raise ValueError("Invalid base stack")

def get_output(key, base_stack, exports):
    value = pulumi_config.get(key)
    if value is None:
        value = base_stack.get_output(key)
    exports[key] = value
    return value



class Core(containers.DeclarativeContainer):

    base_stack = providers.Singleton(
        get_base_stack,
    )

    exports = providers.Singleton(
        lambda: {}
    )

    get_output = providers.Callable(
        get_output,
        base_stack=base_stack,
        exports=exports,
    )

