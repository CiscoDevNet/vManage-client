# vManage-client
[![Python3.8](https://img.shields.io/static/v1?label=Python&logo=Python&color=3776AB&message=3.8)](https://www.python.org/)

vManage client is a package for creating simple and parallel automatic requests via official vManageAPI. It is intended to serve as a multiple session handler (provider, provider as a tenant, tenant). The library is not dependent on environment which is being ran, you just need a connection to any vManage.

## Installation
```console
pip install vmngclient
```

## Hello world example

<details>
    <summary>Python (click to expand)</summary>

```Python
from vmngclient.session import create_vManageSession


base_url = "sandbox-sdwan-2.cisco.com/"
username = "devnetuser"
password = "RG!_Yw919_83"
session = create_vManageSession(url=base_url, username=username, password=password)


>>> "Logged as devnetuser. The session type is SessionType.TENANT"
>>> {'title': 'Cisco vManage', 'version': '20.4.2.1', 'applicationVersion': '20.4R-vbamboo-16-Dec-2021 19:07:17 PST', 'applicationServer': 'vmanage', 'copyright': 'Copyright (c) 2022, Cisco. All rights reserved.', 'time': '2022-12-01 13:45:44', 'timeZone': 'UTC', 'logo': '/dataservice/client/logo.png'}
```
</details>


## User creation example

<details>
    <summary>Python (click to expand)</summary>

```Python
from vmngclient.api.administration import UserAlreadyExistsError, UserApi
from vmngclient.dataclasses import User
from vmngclient.session import Session

user_api = UserApi(provider_session)

test_user = User(
    group=["basic"],
    description="Demo User",
    username="demouser",
    password="password",
    locale="en_US",
    resource_group="global"
)

try:
    user_api.create_user(test_user)
except UserAlreadyExistsError as error:
    print(f"User {username} already exists.")

print(user_api.get_all_users())
```
</details>


## Contributing, reporting issues, seeking support
Please contact authors direcly or via Issues Github page.

## **Enviroment setup**
1. Download Python3.8 or higher.
2. Download repository
    ```
    git clone https://github.com/CiscoDevNet/vManage-client.git
    ```
3. Install poetry v1.1.13

    ```
    pip install poetry==1.1.13
    ```
4. Install dependecies 
    ```
    poetry install
    ```
5. Activate `pre-commit`
    ```
    pre-commit install
    ```

## **Add new feature**
To add new feature create new branch and implement it. Before making a pull request make sure that `pre-commit` passes.
- **Building package for tests**\
    To make a `.whl` file run
    ```
    poetry build
    ```
    Then in `/vManage-client/dist/` directory there is a `.whl` file named `vmngclient-<version>-py3-none-any.whl`, which can be installed by running
    ```
    pip install vmngclient-<version>-py3-none-any.whl
    ```