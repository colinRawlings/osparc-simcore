""" Creates all routes for authentication/authorization subsystem by
    mapping all routes defined under /auth/ path and subsystem's handlers

"""

import logging
from pprint import pformat
from typing import List

from aiohttp import web
from servicelib.aiohttp import openapi
from servicelib.aiohttp.rest_routing import (
    iter_path_operations,
    map_handlers_with_operations,
)

from . import api_keys_handlers
from . import handlers as login_handlers

log = logging.getLogger(__name__)


def create_routes(specs: openapi.Spec) -> List[web.RouteDef]:
    """Creates routes mapping operators_id with handler functions

    :param specs: validated oas
    :type specs: openapi.Spec
    :return: list of web routes for auth
    :rtype: List[web.RouteDef]
    """
    log.debug("Creating %s ", __name__)

    base_path = openapi.get_base_path(specs)

    def include_path(tuple_object):
        _method, path, _operation_id, _tags = tuple_object
        return path.startswith(base_path + "/auth/")

    handlers_map = {
        "auth_register": login_handlers.register,
        "auth_login": login_handlers.login,
        "auth_logout": login_handlers.logout,
        "auth_reset_password": login_handlers.reset_password,
        "auth_reset_password_allowed": login_handlers.reset_password_allowed,
        "auth_change_email": login_handlers.change_email,
        "auth_change_password": login_handlers.change_password,
        "auth_confirmation": login_handlers.email_confirmation,
        "create_api_key": api_keys_handlers.create_api_key,
        "delete_api_key": api_keys_handlers.delete_api_key,
        "list_api_keys": api_keys_handlers.list_api_keys,
    }

    routes = map_handlers_with_operations(
        handlers_map, filter(include_path, iter_path_operations(specs)), strict=True
    )

    log.debug("Mapped auth routes: %s", "\n".join([pformat(r) for r in routes]))

    return routes
