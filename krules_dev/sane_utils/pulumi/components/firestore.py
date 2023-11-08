import pulumi
import pulumi_gcp as gcp

from krules_dev import sane_utils
from krules_dev.sane_utils import inject


class FirestoreDB(pulumi.ComponentResource):
    @inject
    def __init__(self, resource_name: str,
                 firestore_dbname: str = None,
                 firestore_project_id: str = None,
                 firestore_location: str = None,
                 type: str = "FIRESTORE_NATIVE",
                 opts: pulumi.ResourceOptions = None,
                 **kwargs) -> None:
        super().__init__('sane:FirestoreDB', resource_name, None, opts)

        self.db = gcp.firestore.Database(
            resource_name,
            name=firestore_dbname,
            project=firestore_project_id,
            location_id=firestore_location,
            type=type,
            opts=pulumi.ResourceOptions(parent=self),
            **kwargs
        )

        self.register_outputs({})



