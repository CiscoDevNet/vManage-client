import logging
from enum import Enum
from pathlib import PurePath
from typing import Any, Dict, List

from attr import define
from requests_toolbelt.multipart.encoder import MultipartEncoder  # type: ignore

from vmngclient.api.versions_utils import DeviceVersions, RepositoryAPI
from vmngclient.dataclasses import Device
from vmngclient.session import vManageSession

logger = logging.getLogger(__name__)


class Family(Enum):
    VEDGE = "vedge"
    VMANAGE = "vmanage"


class VersionType(Enum):
    VMANAGE = "vmanage"


class DeviceType(Enum):
    CONTROLLER = "controller"
    VEDGE = "vedge"
    VMANAGE = "vmanage"


@define
class InstallSpecification:

    family: Family
    version_type: VersionType
    device_type: DeviceType


class InstallSpecHelper(Enum):
    VMANAGE = InstallSpecification(Family.VMANAGE, VersionType.VMANAGE, DeviceType.VMANAGE)
    VSMART = InstallSpecification(Family.VEDGE, VersionType.VMANAGE, DeviceType.CONTROLLER)
    VBOND = InstallSpecification(Family.VEDGE, VersionType.VMANAGE, DeviceType.CONTROLLER)
    VEDGE = InstallSpecification(Family.VEDGE, VersionType.VMANAGE, DeviceType.VEDGE)
    CEDGE = InstallSpecification(Family.VEDGE, VersionType.VMANAGE, DeviceType.VEDGE)


class SoftwareActionAPI:
    """
    API methods for software actions. All methods
    are exececutable on all device categories.
    """

    def __init__(self, session: vManageSession, device_versions: DeviceVersions, repository: RepositoryAPI) -> None:

        self.session = session
        self.device_versions = device_versions
        self.repository = repository

    def activate_software(self, version_to_activate: str, devices: List[Device]) -> str:
        """
        Method to set choosen version as current version

        Args:
            version_to_activate (str): version to be set as current version

        Returns:
            str: action id
        """

        url = "/dataservice/device/action/changepartition"
        payload = {
            "action": "changepartition",
            "devices": self.device_versions._get_device_list_in(version_to_activate, devices, "available_versions"),
            "deviceType": "vmanage",
        }
        activate = dict(self.session.post(url, json=payload))
        return activate["id"]

    def upgrade_software(
        self,
        devices: List[Device],
        software_image: str,
        install_spec: InstallSpecification,
        reboot: bool,
        sync: bool = True,
    ) -> str:
        """
        Method to install new software

        Args:
            software_image (str): path to software image
            install_spec (InstallSpecification): specification of devices
            on which the action is to be performed
            reboot (bool): reboot device after action end
            sync (bool, optional): Synchronize settings. Defaults to True.

        Raises:
            ValueError: Raise error if downgrade in certain cases

        Returns:
            str: action id
        """

        url = "/dataservice/device/action/install"
        payload: Dict[str, Any] = {
            "action": "install",
            "input": {
                "vEdgeVPN": 0,
                "vSmartVPN": 0,
                "family": install_spec.family.value,
                "version": self.repository.get_image_version(software_image),
                "versionType": install_spec.version_type.value,
                "reboot": reboot,
                "sync": sync,
            },
            "devices": self.device_versions.get_device_list(devices),
            "deviceType": install_spec.device_type.value,
        }
        if install_spec.family.value in (Family.VMANAGE.value, Family.VEDGE.value):
            incorrect_devices = self._downgrade_check(
                payload["devices"],
                payload["input"]["version"],
                install_spec.family.value,
            )
            if incorrect_devices:
                raise ValueError(
                    f"Current version of devices with id's {incorrect_devices} is \
                    higher than upgrade version. Action denied!"
                )
        upgrade = dict(self.session.post(url, json=payload))
        return upgrade["id"]

    def upload_image(self, image_path: str) -> int:
        """
        Upload software image 'tar.gz' to Vmanage
        software repository

        Args:
            image_path (str): path to software image

        Returns:
            str: Response status code
        """
        url = "/dataservice/device/action/software/package"
        encoder = MultipartEncoder(
            fields={'file': (PurePath(image_path).name, open(image_path, 'rb'), 'application/x-gzip')}
        )
        headers = self.session.headers.copy()
        headers.update({"content-type": encoder.content_type})
        response = self.session.post(url, data=encoder, headers=headers)
        return response.status_code

    def _downgrade_check(self, devices, version_to_upgrade: str, family) -> List:
        """
        Check if upgrade operation is not actually a downgrade opeartion.
        If so, in some cases action is being blocked.

        Args:
            version_to_upgrade (str): version to upgrade
            devices_category (DeviceCategory): devices category

        Returns:
            Union[None, List]: [None, list of devices with no permission to downgrade]
        """
        incorrect_devices = []
        devices_versions_repo = self.repository.get_devices_versions_repository(
            self.device_versions.device_category.value
        )
        for dev in devices:
            dev_current_version = str(devices_versions_repo[dev["deviceId"]].current_version)
            splited_version_to_upgrade = version_to_upgrade.split(".")
            for priority, label in enumerate(dev_current_version.split("-")[0].split(".")):
                if str(label) > str(splited_version_to_upgrade[priority]):
                    if family == "vmanage" and label == 2:
                        continue
                    incorrect_devices.append(dev["deviceId"])
                    break
                elif str(label) < str(splited_version_to_upgrade[priority]):
                    break
        return incorrect_devices
