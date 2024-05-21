from typing import Optional

import pulumi
import pulumi_kubernetes as kubernetes
import yaml

from krules_dev import sane_utils
from krules_dev.sane_utils import inject


class GkeSecretProviderClass(kubernetes.apiextensions.CustomResource):

    @inject
    def __init__(self, resource_name: str,
                 secret_name: str,
                 secret_version: str = "latest",
                 provider: str = "gke",
                 path: str = None,
                 # target: str = None,
                 # project_name: str = None,
                 # project_id: str = None,
                 # cluster_project_id: str = None,
                 namespace: str = None,
                 secretmanager_project_id: str = None) -> None:

        self.secret_name = secret_name
        #self.provider = provider
        #self.name = resource_name

        super().__init__(
            "cm-config-secret-provider-class",
            api_version="secrets-store.csi.x-k8s.io/v1",
            kind="SecretProviderClass",
            metadata={
                "name": resource_name,
                "namespace": namespace,
            },
            spec={
                "provider": provider,
                "parameters": {
                    "secrets": yaml.dump([
                        {
                            "resourceName": f"projects/{secretmanager_project_id}/secrets/{sane_utils.name_resource(secret_name)}/versions/{secret_version}",
                            "path": path,
                        }
                    ])
                }

            }
        )

