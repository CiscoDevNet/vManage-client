from __future__ import annotations

import logging
import time
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urljoin

from requests import Response, Session, head
from requests.auth import AuthBase
from requests.exceptions import ConnectionError, HTTPError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed  # type: ignore

from vmngclient.utils.response import response_debug
from vmngclient.vmanage_auth import vManageAuth

logger = logging.getLogger(__name__)

JSON = Union[Dict[str, "JSON"], List["JSON"], str, int, float, bool, None]


class SessionType(Enum):
    PROVIDER = auto()
    TENANT = auto()
    PROVIDER_AS_TENANT = auto()
    NOT_DEFINED = auto()


class UserMode(Enum):
    PROVIDER = "provider"
    TENANT = "tenant"
    NOT_RECOGNIZED = "not recognized"
    NOT_FOUND = "not found"


class ViewMode(Enum):
    PROVIDER = "provider"
    TENANT = "tenant"
    NOT_RECOGNIZED = "not recognized"
    NOT_FOUND = "not found"


class SessionNotCreatedError(Exception):
    pass


def create_vManageSession(
    url: str,
    username: str,
    password: str,
    port: Optional[int] = None,
    subdomain: Optional[str] = None,
) -> vManageSession:
    """Factory function that creates session object based on provided arguments.

    Args:
        url (str): IP address or domain name
        port (int): port
        username (str): username
        password (str): password
        subdomain: subdomain specifying to which view switch when creating provider as a tenant session,
            works only on provider user mode

    Returns:
        Session object

    """

    session = vManageSession(url=url, username=username, password=password, port=port, subdomain=subdomain)
    session.auth = vManageAuth(session.base_url, username, password, verify=False)

    if subdomain:
        tenant_id = session.get_tenant_id()
        vsession_id = session.get_virtual_session_id(tenant_id)
        session.headers.update({"VSessionId": vsession_id})
    server_info = session.server()

    try:
        user_mode = UserMode(server_info.get("userMode", "not found"))
    except ValueError:
        user_mode = UserMode.NOT_RECOGNIZED
        logger.warning(f"Unrecognized user mode is: '{server_info.get('userMode')}'")

    try:
        view_mode = ViewMode(server_info.get("viewMode", "not found"))
    except ValueError:
        view_mode = ViewMode.NOT_RECOGNIZED
        logger.warning(f"Unrecognized user mode is: '{server_info.get('viewMode')}'")
    if user_mode is UserMode.TENANT and not subdomain and view_mode is ViewMode.TENANT:
        session.session_type = SessionType.TENANT
    elif user_mode is UserMode.PROVIDER and not subdomain and view_mode is ViewMode.PROVIDER:
        session.session_type = SessionType.PROVIDER
    elif user_mode is UserMode.PROVIDER and view_mode is ViewMode.TENANT:

        session.session_type = SessionType.PROVIDER_AS_TENANT
    elif user_mode is UserMode.TENANT and subdomain:
        raise SessionNotCreatedError(
            f"Session not created. Subdomain {subdomain} passed to tenant session, "
            "cannot switch to tenant from tenant user mode."
        )
    else:
        session.session_type = SessionType.NOT_DEFINED
        logger.warning(
            f"Session created with {user_mode.value} user mode and {view_mode.value} view mode.\n"
            f"Session type set to not defined"
        )
    logger.info(f"Logged as {username}. The session type is {session.session_type}")
    return session


class vManageSession(Session):
    """Base class for API sessions for vManage client.

    Defines methods and handles session connectivity available for provider, provider as tenant, and tenant.

    Args:
        url: IP address or domain name, i.e. '10.0.1.200' or 'example.com'
        port: port
        username: username
        password: password
    """

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        verify: bool = False,
        port: Optional[int] = None,
        subdomain: Optional[str] = None,
        auth: Optional[AuthBase] = None,
    ):
        self.url = url
        self.port = port
        self.base_url = self.__create_base_url()
        self.username = username
        self.password = password
        self.port = port
        self.subdomain = subdomain

        self.session_type = SessionType.NOT_DEFINED

        if not self.check_vmanage_server_connection():
            raise ConnectionError("Vmanage server is not available")

        super(vManageSession, self).__init__()
        self.__prepare_session(verify, auth)

    def request(self, method, url, *args, **kwargs) -> Any:
        full_url = self.get_full_url(url)
        response = super(vManageSession, self).request(method, full_url, *args, **kwargs)
        logger.debug(response_debug(response))
        try:
            response.raise_for_status()
        except HTTPError as error:
            logger.debug(error)
            if response.status_code == 403:
                logger.info(f"User {self.username} is unauthorized for method {method} {full_url}")
            else:
                raise error
        return response

    def get_full_url(self, url_path: str) -> str:
        """Returns base API url plus given url path."""
        return urljoin(self.base_url, url_path)

    def __create_base_url(self) -> str:
        """Creates base url based on ip address or domain and port if provided.

        Returns:
            str: Base url shared for every request.
        """
        if self.port:
            return f"https://{self.url}:{self.port}"
        return f"https://{self.url}"

    def about(self) -> Dict:
        return self.get_data(url="/dataservice/client/about")

    def server(self) -> Dict:
        return self.get_data(url="/dataservice/client/server")

    def get_data(self, url: str) -> Any:
        return self.get_json(url)["data"]

    def get_json(self, url: str) -> Any:
        response = self.get(url)
        return response.json()

    def get_file(self, url: str, filename: Path) -> Response:
        """Get a file using session get.

        Args:
            url: dataservice api.
            filename: Filename to write download file to.

        Returns:
            http response.

        Example usage:
            response = self.session.get_file(url, filename)

        """
        with self.get(url) as response:
            with open(filename, "wb") as file:
                file.write(response.content)
        return response

    def wait_for_server_reachability(self, retries: int, delay: int, initial_delay: int = 0) -> bool:
        """Checks if vManage API is reachable by sending server request.

        Retries on HTTPError for specified number of times.
        Delays between each request are configurable,
        It is intended to be used as a probe, so it doesn't raise original error from exception.

        Args:
            retries: total number of retires
            delay: time to wait between each retry
            initial_delay: time before sending first request

        Returns:
            Bool: True if device is reachable, False if not
        """

        def _log_exception(retry_state):
            logger.error(f"Cannot reach server, original exception: {retry_state.outcome.exception()}")
            return False

        if initial_delay:
            time.sleep(initial_delay)

        @retry(
            wait=wait_fixed(delay),
            retry=retry_if_exception_type(HTTPError),
            stop=stop_after_attempt(retries),
            retry_error_callback=_log_exception,
        )
        def _send_server_request():
            return self.server()

        return True if _send_server_request() else False

    def get_tenant_id(self) -> str:
        """Gets tenant UUID for its subdomain.

        Returns:
            Tenant UUID.
        """
        tenants = self.get_data(url="/dataservice/tenant")
        tenant_id = [tenant.get("tenantId", None) for tenant in tenants if tenant["subDomain"] == self.subdomain][0]
        return tenant_id

    def get_virtual_session_id(self, tenant_id: str) -> str:
        """Get VSessionId for a specific tenant

        Note: In a multitenant vManage system, this API is only available in the Provider view.

        Args:
            tenant_id: provider or tenant UUID
        Returns:
            Virtual session token
        """
        url_path = f"/dataservice/tenant/{tenant_id}/vsessionid"
        response = self.post(url_path)
        return response.json()["VSessionId"]

    def __prepare_session(self, verify: bool, auth: Optional[AuthBase]) -> None:
        self.auth = auth
        self.verify = verify

    def check_vmanage_server_connection(self) -> bool:
        try:
            url = str(self.base_url).replace("https", "http")
            if self.port:
                url = url.replace(str(f":{self.port}"), "")
            head(url, timeout=2)
        except ConnectionError:
            return False
        else:
            return True

    def __str__(self) -> str:
        return f"{self.username}@{self.base_url}"

    def __repr__(self):
        return (
            f"{self.__class__.__name__}('{self.url}', '{self.username}', '{self.password}', port={self.port}, "
            f"subdomain='{self.subdomain}')"
        )

    def __eq__(self, other):
        if isinstance(other, vManageSession):
            comparison_list = [
                self.url == other.url,
                self.username == other.username,
                self.password == other.password,
                self.port == other.port,
                str(self.subdomain) == str(other.subdomain),
            ]
            return True if all(comparison_list) else False
        return False
