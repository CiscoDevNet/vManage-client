import logging
import socket
import ssl
import time
from http import HTTPStatus
from http.client import HTTPException, HTTPResponse
from ipaddress import AddressValueError, IPv4Address
from json import dumps, loads
from logging import Logger
from typing import Any, Dict, List, Optional, Union, cast
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from vmngclient.utils.creation_tools import get_logger_name

logger = logging.getLogger(get_logger_name(__name__))


class Session:
    def __init__(self, ip_address: str, port: int, username: str, password: str, timeout: int = 30) -> None:
        self.base_url = self.__create_base_url(ip_address, port)
        self.username = username
        self.password = password
        self.timeout = timeout

        self.ctx = self.__get_context()

        # TODO session headers as a function and serializable class
        self.session_headers: Dict[str, str] = dict()

    def get_full_url(self, url: str) -> str:
        """Returns base API URL plus relative URL.

        Args:
            url: relative API URL, i.e. '/dataservice/devices'

        Returns:
            str: Absolute API URL, i.e. 'https://10.0.1.200:8443/dataservice/devices'
        """
        return f'{self.base_url}{url}'

    def relogin_request(self, method: str, url: str, data: Union[dict, list, None] = None) -> HTTPResponse:
        """Generic method to send request to vManage API.

        Args:
            method: API method, i.e. POST, GET
            url: API endpoint without base URL
            data: (optional) Dictionary, list of tuples, bytes, or file-like
              object to send in the body of the request

        Returns:
            Response object
        """
        full_url = self.get_full_url(url)
        logger.debug(f'{method} {full_url}')
        response = self.session_request(method, full_url, data)
        if response.status == HTTPStatus.OK and 'set-cookie' in response.headers:
            self.session_headers = {
                name: value
                for name, value in self.session_headers.items()
                if name not in ['cookie', 'x-xsrf-token', 'VSessionId', 'content-type']
            }
            # in this case re-login is necessary because session is expired
            self.login()
            response = self.session_request(method, full_url, data)

        return response

    def get(self, url: str) -> HTTPResponse:
        """Send HTTP GET request using session headers.

        Used by methods: get_json.

        Args:
            url: API endpoint without base URL

        Returns:
            Response object
        """
        return self.relogin_request('GET', url)

    def decode_json(self, response: HTTPResponse) -> Union[dict, list]:
        """Try parsing API response as JSON and produce readable error message on failure.

        Args:
            response: response object

        Returns:
            Parsed data from JSON
        """
        response_text = response.read().decode('utf-8')
        try:
            json = loads(response_text)
            return json
        except ValueError as err:
            logger.error(f'Response status: {response.status}')
            logger.error(f'Invalid JSON response: {response_text}')
            raise err

    def get_json(self, url: str) -> Union[dict, list]:
        """Sends HTTP GET request and return parsed JSON data.

        Used by methods: get_data.

        Args:
            url: API endpoint without base URL

        Returns:
            JSON data parsed from response
        """
        return self.decode_json(self.get(url))

    def get_data(self, url: str) -> Union[list, dict]:
        """Sends HTTP GET request and return 'data' property from parsed JSON data.

        Args:
            url: API endpoint without base URL

        Returns:
            'data' property from the JSON response
        """
        json = cast(dict, self.get_json(url))
        assert 'data' in json, "Expecting data property in JSON response"

        return json['data']

    def post(self, url: str, data: Union[dict, list, None] = None) -> HTTPResponse:
        """Sends HTTP POST request using session headers.

        Used by methods: post_json, login.

        Args:
            url: API endpoint without base URL
            data: (optional) Dictionary, list of tuples, bytes, or file-like
              object to send in the body of the request

        Returns:
            response object
        """
        return self.relogin_request('POST', url, data)

    def post_json(self, url: str, data: Union[dict, list, None] = None) -> Union[dict, list]:
        """Sends HTTP POST request and return parsed JSON data.

        Args:
            url: API endpoint without base URL
            data: request payload

        Returns:
            JSON data parsed from response
        """
        return self.decode_json(self.post(url, data))

    def post_data(self, url: str, data: Optional[dict] = None) -> Union[list, dict]:
        """Sends HTTP POST request and return 'data' property from parsed JSON data.

        Args:
            url: API endpoint without base URL

        Returns:
            'data' property from the JSON response
        """
        json = cast(dict, self.post_json(url, data))
        assert 'data' in json, "Expecting data property in JSON response"

        return json['data']

    def put(self, url: str, data: Optional[dict] = None) -> HTTPResponse:
        """Sends HTTP PUT request using session headers.

        Used by methods: put_json.

        Args:
            url: API endpoint without base URL
            data: (optional) Dictionary, list of tuples, bytes, or file-like
              object to send in the body of the request

        Returns:
            response object
        """
        return self.relogin_request('PUT', url, data)

    def put_json(self, url: str, data: Optional[dict] = None) -> Union[list, dict]:
        """Sends HTTP PUT request and return parsed JSON data.

        Args:
            url: API endpoint without base URL
            data: request payload

        Returns:
            JSON data parsed from response
        """
        return self.decode_json(self.put(url, data))

    def put_data(self, url: str, data: Optional[dict] = None) -> Union[list, dict]:
        """Sends HTTP PUT request and return 'data' property from parsed JSON data.

        Args:
            url: API endpoint without base URL

        Returns:
            'data' property from the JSON response
        """
        json = cast(dict, self.put_json(url, data))
        assert 'data' in json, "Expecting data property in JSON response"

        return json['data']

    def delete(self, url: str) -> HTTPResponse:
        """Sends HTTP DELETE request for delete resource for example admin_tech file.

        Args:
            url: API endpoint without base URL

        Returns:
            response object
        """
        return self.relogin_request('DELETE', url)

    def login(self) -> None:
        """Login to vManage API using self username and password.

        Returns:
            Dictionary with Session headers.  # TODO
        """
        # send no session headers to login
        response = urlopen(
            Request(
                self.get_full_url('/j_security_check'),
                data=urlencode(
                    {
                        'j_username': self.username,
                        'j_password': self.password,
                    }
                ).encode('utf-8'),
                headers=self.session_headers,
            ),
            context=self.ctx,
            timeout=self.timeout,
        )

        assert 'set-cookie' in response.headers, 'Authentication error: cookie not found'

        self.session_headers['content-type'] = 'application/json'
        self.session_headers['cookie'] = response.headers['set-cookie']

        # use session_request instead of relogin_request to avoid infinite loop
        self.session_headers['x-xsrf-token'] = (
            self.session_request('GET', self.get_full_url('/dataservice/client/token')).read().decode('ascii')
        )

    def session_request(self, method: str, full_url: str, data: Union[dict, list, None] = None) -> HTTPResponse:
        available_methods = ["GET", "POST", "PUT", "PATCH", "HEAD", "DELETE"]
        assert method in available_methods, f"{method} is not supported. Available methods: {available_methods}."
        encoded = dumps(data).encode() if data else None
        try:
            return urlopen(
                Request(full_url, method=method, data=encoded, headers=self.session_headers),
                context=self.ctx,
                timeout=self.timeout,
            )
        except HTTPError as error:
            if error.code == 503:  # Catch Service Unavailable
                logger.warning("Server not ready to handle the request.")
                raise error
            logger.error(f"There was a problem with {method} method. URL: {full_url}. Error: {error}")
            raise error
        except socket.timeout as error:
            logger.error("Request timeout!")
            raise error
        except HTTPException as error:
            logger.error(error)
            raise error

    def __get_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def __create_base_url(self, ip_address: str, port: int) -> str:
        """Creates base url based on ip address and port.

        Args:
            ip_address (str): Ip address of reachable vManage.
            port (int): Port of reachable vManage.

        Returns:
            str: Base url shared for every request.
        """
        try:
            IPv4Address(ip_address)
        except (AddressValueError, AttributeError) as error_info:
            logger.info(f"Please provide correct IPv4 address. Error info: {error_info}")
        return f"https://{ip_address}:{port}"

    def __get_logger(self) -> Logger:
        """TODO: method should configure self sufficent vManage-client logger

        Returns:
            Logger: Configured logger.
        """
        logger = logging.getLogger(get_logger_name(__name__))
        logger.debug("Creating vManage-client logger")
        return logger

    def about(self) -> Union[List[Any], Dict[Any, Any]]:
        response = self.get_data(url="/dataservice/client/about")
        return response

    def server(self) -> Union[List[Any], Dict[Any, Any]]:
        response = self.get_data(url="/dataservice/client/server")
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
            logger.error(f"Cannot reach server, orignial exception: {retry_state.outcome.exception()}")
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

    def __str__(self) -> str:
        return f"{self.username}@{self.base_url}"


class ProviderAsTenantSession(Session):
    """vManage API client logged in as provider acting as tenant.

    Attributes:
        ip_address: IP address, i.e. '10.0.1.200'
        port: port
        username: provider username
        password: secret
        subdomain: tenant subdomain, i.e. 'apple.fruits.com'
    """

    def __init__(
        self, ip_address: str, port: int, username: str, password: str, subdomain: str, timeout: int = 30
    ) -> None:
        self.subdomain = subdomain
        super().__init__(ip_address, port, username, password, timeout)
        # self._name = f'{self._name} vSession for {subdomain}'

    def login(self) -> None:
        """Logs in to vManage API as Provider using username/password and switch to Tenant.

        Returns:
            ProviderAsTenantSession object  TODO
        """
        super().login()
        self.switch_to_tenant()

    def get_tenant_id(self) -> str:
        """Gets tenant UUID for tenant subdomain.

        Returns:
            tenant UUID
        """
        tenants = self.get_data('/dataservice/tenant')
        tenant_ids = [tenant.get('tenantId') for tenant in tenants if tenant['subDomain'] == self.subdomain]
        assert len(tenant_ids) > 0, f"Tenant not found for subdomain: {self.subdomain}"
        return tenant_ids[0]

    def create_vsession(self, tenant_id: str) -> str:
        """Creates virtual session for tenant.

        Args:
            tenant_id: provider or tenant UUID

        Returns:
            virtual session token
        """
        response = cast(dict, self.post_json(f'/dataservice/tenant/{tenant_id}/vsessionid'))
        assert 'VSessionId' in response, "Invalid vsessionid response"
        return response['VSessionId']

    def switch_to_tenant(self) -> None:
        """As provider impersonate tenant session."""
        tenant_id = self.get_tenant_id()
        vsession_id = self.create_vsession(tenant_id)
        assert vsession_id != '', 'Switch to tenant expecting VSessionId to not be empty'
        self.session_headers['VSessionId'] = vsession_id

    def switch_to_provider(self) -> None:
        """Switch back to provider session after impersonating tenant."""
        data = cast(dict, self.server())
        assert 'providerId' in data, "Invalid vsessionid response"
        vsession_id = self.create_vsession(data['providerId'])
        assert vsession_id == '', 'Switch to provider expecting VSessionId to be empty'
        del self.session_headers['VSessionId']


class TenantSession(Session):
    """vManage API client logged as a tenant.

    Attributes:
        base_url: TODO.
        domain: Tenant domain, i.e. 'apple.fruits.com'.
        username: Tenant username.
        password: Tenant password.
        timeout: TODO. Defaults to 0.
    """

    def __init__(
        self, ip_address: str, domain: str, port: int, username: str, password: str, timeout: int = 0, logger=None
    ) -> None:
        self.domain = domain
        # TODO How to remove an ip address? Do we need an ip for tenant?
        super().__init__(ip_address, port, username, password)
        # self.base_url = "https://apple.fruits.com:10100"

    def login(self) -> None:
        """Login to vManage API as Tenant using username/password and set the session headers.

        Returns:
            TenantSession object
        """

        # TODO DO we need header when we use domain?
        self.session_headers['Host'] = self.domain
        super().login()

    def __str__(self) -> str:
        return f"{self.username}@{self.domain}"