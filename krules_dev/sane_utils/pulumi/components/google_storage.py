import pulumi_gcp as gcp

from krules_dev import sane_utils


class Bucket(gcp.storage.Bucket):

    def __init__(self, resource_name: str,
                 *args, **kwargs) -> None:
        if "project" not in kwargs:
            kwargs['project'] = sane_utils.get_project_id()

        if "location" not in kwargs:
            kwargs['location'] = "EU"

        if "name" not in kwargs:
            kwargs['name'] = sane_utils.name_resource(resource_name)

        super().__init__(resource_name, *args, **kwargs)
