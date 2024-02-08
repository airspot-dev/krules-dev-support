import hashlib
import inspect
import os
from typing import Callable

from . import get_var_for_target, get_targets_info
from . import check_env
from .. import sane_utils


def get_target() -> str:
    target, _ = get_targets_info()
    return target

def get_app_name() -> str:
    if "APP_NAME" not in os.environ:
        caller_frame = inspect.stack()[1]
        caller_path = caller_frame.filename
        return os.path.basename(os.path.dirname(os.path.abspath(caller_path)))
    return os.environ["APP_NAME"]

def inject(function):
    def _wraps(*args, **kwargs):
        # import pdb; pdb.set_trace()
        sig = inspect.signature(function)

        def _set(k: str, c: Callable, *c_args, **c_kwargs):
            if k in sig.parameters and kwargs.get(k) is None:
                kwargs[k] = c(*c_args, **c_kwargs)

        _set("target", get_target)
        _set("project_name", check_env, "project_name")
        _set("project_id", get_project_id)
        _set("cluster_project_id", get_cluster_project_id)
        _set("region", get_region)
        _set("namespace", get_namespace)
        _set("firestore_database", get_firestore_database)
        _set("firestore_project_id", get_firestore_project_id)
        _set("firestore_location", get_firestore_location)
        _set("firestore_id", get_firestore_id)
        _set("secretmanager_project_id", get_secretmanager_project_id)
        return function(*args, **kwargs)

    return _wraps


@inject
def is_shared_project(target=None) -> bool:
    return bool(eval(get_var_for_target("shared_project", target=target, default="True")))


@inject
def name_resource(resource_name, target=None, force=False) -> str:
    if is_shared_project(target=target) or force is True:
        project_name = check_env("project_name")
        if resource_name == project_name:
            return f"{project_name}-{target}"
        return f"{project_name}-{resource_name}-{target}"

    return resource_name


@inject
def get_hashed_resource_name(resource_name, target=None, prefix=""):
    trans_tbl = str.maketrans(dict.fromkeys('aeiouAEIOU-_'))
    m = hashlib.sha256()
    m.update(sane_utils.name_resource(resource_name, target=target, force=True).encode())
    account_id = f"ksa-{resource_name.translate(trans_tbl)}{m.hexdigest()}"[:28]
    return account_id


@inject
def get_project_id(target=None) -> str:
    return get_var_for_target("project_id", target)


@inject
def get_cluster_project_id(target=None) -> str:
    return get_var_for_target("cluster_project_id", target=target, default=get_project_id())


@inject
def get_region(target=None) -> str:
    return get_var_for_target("region", target=target)


@inject
def get_namespace(project_name=None, target=None) -> str:
    return get_var_for_target("namespace",
                              default=f"{project_name}-{target}")


@inject
def get_firestore_database(project_name=None, target=None) -> str:
    return get_var_for_target("firestore_database", target=target, default="(default)")


@inject
def get_firestore_project_id(target=None) -> str:
    return get_var_for_target("firestore_project_id", target=target, default=get_project_id())


@inject
def get_firestore_location(target=None) -> str:
    return get_var_for_target("firestore_location", target=target, default=get_region())


@inject
def get_firestore_id(target=None, firestore_project_id=None, firestore_location=None, firestore_database=None) -> str:
    return f"projects/{firestore_project_id}/databases/{firestore_database}"


@inject
def get_secretmanager_project_id(target=None) -> str:
    return get_var_for_target("secretmanager_project_id", target=target, default=get_project_id())
