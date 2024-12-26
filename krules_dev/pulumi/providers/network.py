from typing import Optional
import platform
import pulumi
from pulumi.dynamic import Resource, ResourceProvider, CreateResult
import subprocess


class NetworkInterfaceProvider(ResourceProvider):
    def create(self, props):
        os_type = platform.system().lower()
        if os_type not in ["linux", "darwin"]:
            raise Exception(
                f"Operating system {os_type} is not supported. Only Linux and macOS are supported."
            )

        interface_name = props.get("name")
        ip_address = props.get("ip_address")

        try:
            if os_type == "linux":
                # Linux implementation using ip command
                subprocess.run(
                    ["sudo", "ip", "link", "add", interface_name, "type", "dummy"],
                    check=True,
                    capture_output=True,
                )

                subprocess.run(
                    ["sudo", "ip", "link", "set", interface_name, "up"],
                    check=True,
                    capture_output=True,
                )

                if ip_address:
                    subprocess.run(
                        [
                            "sudo",
                            "ip",
                            "addr",
                            "add",
                            ip_address,
                            "dev",
                            interface_name,
                        ],
                        check=True,
                        capture_output=True,
                    )

            elif os_type == "darwin":
                # macOS implementation using ifconfig
                # Create a bridge interface as macOS doesn't support dummy interfaces directly
                subprocess.run(
                    ["sudo", "ifconfig", "bridge", "create"],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                # Get the created bridge name (usually bridgeX where X is a number)
                result = subprocess.run(
                    ["ifconfig", "-a"], check=True, capture_output=True, text=True
                )

                # Find the last created bridge interface
                bridges = [
                    line.split(":")[0]
                    for line in result.stdout.split("\n")
                    if line.startswith("bridge")
                ]
                if not bridges:
                    raise Exception("Failed to create bridge interface")

                created_interface = bridges[-1]

                # Rename the interface if a specific name was requested
                if interface_name != created_interface:
                    subprocess.run(
                        ["sudo", "ifconfig", created_interface, "name", interface_name],
                        check=True,
                        capture_output=True,
                    )

                # Set interface up
                subprocess.run(
                    ["sudo", "ifconfig", interface_name, "up"],
                    check=True,
                    capture_output=True,
                )

                # Assign IP address if provided
                if ip_address:
                    # Strip CIDR notation for ifconfig
                    ip_parts = ip_address.split("/")
                    bare_ip = ip_parts[0]
                    subprocess.run(
                        ["sudo", "ifconfig", interface_name, "inet", bare_ip],
                        check=True,
                        capture_output=True,
                    )

                    # If CIDR notation was provided, calculate and set netmask
                    if len(ip_parts) > 1:
                        prefix_length = int(ip_parts[1])
                        netmask = ".".join(
                            [
                                str((0xFFFFFFFF << (32 - prefix_length) >> i) & 0xFF)
                                for i in [24, 16, 8, 0]
                            ]
                        )
                        subprocess.run(
                            ["sudo", "ifconfig", interface_name, "netmask", netmask],
                            check=True,
                            capture_output=True,
                        )

            return CreateResult(
                id_=interface_name,
                outs={
                    "name": interface_name,
                    "ip_address": ip_address,
                    "os_type": os_type,
                },
            )
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to create network interface: {e.stderr}")
        except Exception as e:
            raise Exception(f"Error creating network interface: {str(e)}")

    def delete(self, id, props):
        os_type = platform.system().lower()
        try:
            if os_type == "linux":
                subprocess.run(
                    ["sudo", "ip", "link", "delete", id],
                    check=True,
                    capture_output=True,
                )
            elif os_type == "darwin":
                subprocess.run(
                    ["sudo", "ip", "delete", id], check=True, capture_output=True
                )
        except subprocess.CalledProcessError as e:
            # Log error but don't raise exception as resource might already be deleted
            print(f"Warning: Error while deleting interface {id}: {e.stderr}")


class NetworkInterface(Resource):
    name: pulumi.Output[str]
    ip_address: pulumi.Output[str]
    os_type: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        ip_address: Optional[str] = None,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        """
        Create a local network interface.

        Args:
            name: Name of the network interface
            ip_address: Optional IP address to assign to the interface (supports CIDR notation)
            opts: Optional resource options

        Note:
            - On Linux, creates a dummy interface
            - On macOS, creates a bridge interface as dummy interfaces are not supported
            - Requires sudo privileges
        """
        props = {"name": name, "ip_address": ip_address}

        super().__init__(NetworkInterfaceProvider(), name, props, opts)


# Example usage:
if __name__ == "__main__":
    interface = NetworkInterface("test-interface", ip_address="192.168.1.100/24")

    # Export the interface name
    pulumi.export("interface_name", interface.name)
    pulumi.export("interface_ip", interface.ip_address)
    pulumi.export("os_type", interface.os_type)
