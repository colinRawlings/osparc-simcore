import re
from typing import Optional, TypedDict

from aiohttp import web
from aiohttp.test_utils import TestClient
from simcore_service_webserver.db_models import UserRole, UserStatus
from simcore_service_webserver.login.registration import create_invitation
from simcore_service_webserver.login.settings import LoginOptions, get_plugin_options
from simcore_service_webserver.login.storage import AsyncpgStorage, get_plugin_storage
from simcore_service_webserver.login.utils import encrypt_password, get_random_string
from yarl import URL

from .utils_assert import assert_status


# WARNING: DO NOT use UserDict is already in https://docs.python.org/3/library/collections.html#collections.UserDictclass UserRowDict(TypedDict):
# NOTE: this is modified dict version of packages/postgres-database/src/simcore_postgres_database/models/users.py for testing purposes
class _UserInfoDictRequired(TypedDict, total=True):
    id: int
    name: str
    email: str
    raw_password: str
    status: UserStatus
    role: UserRole


class UserInfoDict(_UserInfoDictRequired, total=False):
    created_ip: int
    password_hash: str


TEST_MARKS = re.compile(r"TEST (\w+):(.*)")


def parse_test_marks(text):
    """Checs for marks as

    TEST name:123123
    TEST link:some-value
    """
    marks = {}
    for m in TEST_MARKS.finditer(text):
        key, value = m.groups()
        marks[key] = value.strip()
    return marks


def parse_link(text):
    link = parse_test_marks(text)["link"]
    return URL(link).path


async def create_user(db: AsyncpgStorage, data=None) -> UserInfoDict:
    data = data or {}
    password = get_random_string(10)
    params = {
        "name": get_random_string(10),
        "email": "{}@gmail.com".format(get_random_string(10)),
        "password_hash": encrypt_password(password),
    }
    params.update(data)
    params.setdefault("status", UserStatus.ACTIVE.name)
    params.setdefault("role", UserRole.USER.name)
    params.setdefault("created_ip", "127.0.0.1")
    user = await db.create_user(params)
    user["raw_password"] = password
    return user


async def log_client_in(
    client: TestClient, user_data=None, *, enable_check=True
) -> UserInfoDict:
    # creates user directly in db
    assert client.app
    db: AsyncpgStorage = get_plugin_storage(client.app)
    cfg: LoginOptions = get_plugin_options(client.app)

    user = await create_user(db, user_data)

    # login
    url = client.app.router["auth_login"].url_for()
    r = await client.post(
        str(url),
        json={
            "email": user["email"],
            "password": user["raw_password"],
        },
    )

    if enable_check:
        await assert_status(r, web.HTTPOk, cfg.MSG_LOGGED_IN)

    return user


class NewUser:
    def __init__(self, params=None, app: Optional[web.Application] = None):
        self.params = params
        self.user = None
        assert app
        self.db = get_plugin_storage(app)

    async def __aenter__(self):
        self.user = await create_user(self.db, self.params)
        return self.user

    async def __aexit__(self, *args):
        await self.db.delete_user(self.user)


class LoggedUser(NewUser):
    def __init__(self, client, params=None, *, check_if_succeeds=True):
        super().__init__(params, client.app)
        self.client = client
        self.enable_check = check_if_succeeds

    async def __aenter__(self) -> UserInfoDict:
        self.user = await log_client_in(
            self.client, self.params, enable_check=self.enable_check
        )
        return self.user


class NewInvitation(NewUser):
    def __init__(self, client: TestClient, guest="", host=None):
        assert client.app
        super().__init__(host, client.app)
        self.client = client
        self.guest = guest or get_random_string(10)
        self.confirmation = None

    async def __aenter__(self):
        # creates host user
        assert self.client.app
        db: AsyncpgStorage = get_plugin_storage(self.client.app)
        self.user = await create_user(db, self.params)

        self.confirmation = await create_invitation(self.user, self.guest, self.db)
        return self.confirmation

    async def __aexit__(self, *args):
        if await self.db.get_confirmation(self.confirmation):
            await self.db.delete_confirmation(self.confirmation)
