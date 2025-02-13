from asyncio.log import logger
from contextlib import asynccontextmanager
from typing import Optional, Union

import redis
from aiohttp import web
from models_library.projects import ProjectID
from models_library.projects_state import Owner, ProjectLocked, ProjectStatus
from redis.asyncio.lock import Lock

from ..redis import get_redis_lock_manager_client
from ..users_api import UserNameDict
from .projects_exceptions import ProjectLockError

PROJECT_REDIS_LOCK_KEY: str = "project_lock:{}"

ProjectLock = Lock


@asynccontextmanager
async def lock_project(
    app: web.Application,
    project_uuid: Union[str, ProjectID],
    status: ProjectStatus,
    user_id: int,
    user_name: UserNameDict,
):
    """returns a distributed redis lock on the project defined by its UUID.
    NOTE: can be used as a context manager

    try:
        async with lock_project(app, project_uuid, ProjectStatus.CLOSING, user_id, user_name):
            close_project(project_uuid) # do something with the project that requires the project to be locked


    except ProjectLockError:
        pass # the lock could not be acquired

    """
    redis_lock = get_redis_lock_manager_client(app).lock(
        PROJECT_REDIS_LOCK_KEY.format(project_uuid)
    )
    try:
        if not await redis_lock.acquire(
            blocking=False,
            token=ProjectLocked(
                value=True,
                owner=Owner(user_id=user_id, **user_name),
                status=status,
            ).json(),
        ):
            raise ProjectLockError(
                f"Lock for project {project_uuid!r} user {user_id!r} could not be acquired"
            )
        yield
    finally:
        # let's ensure we release that stuff
        try:
            if await redis_lock.owned():
                await redis_lock.release()
        except (redis.exceptions.LockError, redis.exceptions.LockNotOwnedError) as exc:
            logger.warning(
                "releasing %s unexpectedly raised an exception: %s",
                f"{redis_lock=!r}",
                f"{exc}",
            )


async def is_project_locked(
    app: web.Application, project_uuid: Union[str, ProjectID]
) -> bool:
    redis_lock = get_redis_lock_manager_client(app).lock(
        PROJECT_REDIS_LOCK_KEY.format(project_uuid)
    )
    return await redis_lock.locked()


async def get_project_locked_state(
    app: web.Application, project_uuid: Union[str, ProjectID]
) -> Optional[ProjectLocked]:
    """returns the ProjectLocked object if the project is locked"""
    if await is_project_locked(app, project_uuid):
        redis_locks_client = get_redis_lock_manager_client(app)

        if lock_value := await redis_locks_client.get(
            PROJECT_REDIS_LOCK_KEY.format(project_uuid)
        ):
            return ProjectLocked.parse_raw(lock_value)
