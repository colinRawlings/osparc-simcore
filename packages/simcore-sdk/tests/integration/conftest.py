# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name
# pylint:disable=too-many-arguments

import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Tuple
from urllib.parse import quote_plus
from uuid import uuid4

import np_helpers
import pytest
import sqlalchemy as sa
from aiohttp import ClientSession
from pytest_simcore.helpers.rawdata_fakers import random_project, random_user
from settings_library.r_clone import RCloneSettings, S3Provider
from simcore_postgres_database.models.comp_pipeline import comp_pipeline
from simcore_postgres_database.models.comp_tasks import comp_tasks
from simcore_postgres_database.models.projects import projects
from simcore_postgres_database.models.users import users
from simcore_sdk.node_ports import node_config
from simcore_sdk.node_ports_common.r_clone import is_r_clone_available
from yarl import URL


def _set_configuration(
    create_task: Callable[..., str],
    project_id: str,
    node_id: str,
    json_configuration: str,
) -> Dict[str, Any]:
    json_configuration = json_configuration.replace("SIMCORE_NODE_UUID", f"{node_id}")
    configuration = json.loads(json_configuration)
    create_task(project_id, node_id, **configuration)
    return configuration


def _assign_config(
    config_dict: dict, port_type: str, entries: List[Tuple[str, str, Any]]
):
    if entries is None:
        return
    for entry in entries:

        config_dict["schema"][port_type].update(
            {
                entry[0]: {
                    "label": "some label",
                    "description": "some description",
                    "displayOrder": 2,
                    "type": entry[1],
                }
            }
        )
        if not entry[2] is None:
            config_dict[port_type].update({entry[0]: entry[2]})


@pytest.fixture
def user_id(postgres_db: sa.engine.Engine) -> Iterable[int]:
    # inject user in db

    # NOTE: Ideally this (and next fixture) should be done via webserver API but at this point
    # in time, the webserver service would bring more dependencies to other services
    # which would turn this test too complex.

    # pylint: disable=no-value-for-parameter
    stmt = users.insert().values(**random_user(name="test")).returning(users.c.id)
    print(f"{stmt}")
    with postgres_db.connect() as conn:
        result = conn.execute(stmt)
        [usr_id] = result.fetchone()

    yield usr_id

    with postgres_db.connect() as conn:
        conn.execute(users.delete().where(users.c.id == usr_id))


@pytest.fixture
def project_id(user_id: int, postgres_db: sa.engine.Engine) -> Iterable[str]:
    # inject project for user in db. This will give user_id, the full project's ownership

    # pylint: disable=no-value-for-parameter
    stmt = (
        projects.insert()
        .values(**random_project(prj_owner=user_id))
        .returning(projects.c.uuid)
    )
    print(f"{stmt}")
    with postgres_db.connect() as conn:
        result = conn.execute(stmt)
        [prj_uuid] = result.fetchone()

    yield prj_uuid

    with postgres_db.connect() as conn:
        conn.execute(projects.delete().where(projects.c.uuid == prj_uuid))


@pytest.fixture(scope="module")
def node_uuid() -> str:
    return f"{uuid4()}"


@pytest.fixture(scope="session")
def s3_simcore_location() -> str:
    return np_helpers.SIMCORE_STORE


@pytest.fixture
async def filemanager_cfg(
    storage_service: URL,
    testing_environ_vars: Dict,
    user_id: str,
    bucket: str,
) -> None:
    node_config.STORAGE_ENDPOINT = f"{storage_service.host}:{storage_service.port}"
    node_config.BUCKET = bucket


@pytest.fixture
def create_valid_file_uuid(project_id: str, node_uuid: str) -> Callable[[Path], str]:
    def _create(file_path: Path) -> str:
        return np_helpers.file_uuid(file_path, project_id, node_uuid)

    return _create


@pytest.fixture()
def default_configuration(
    node_ports_config: None,
    bucket: str,
    create_pipeline: Callable[[str], str],
    create_task: Callable[..., str],
    default_configuration_file: Path,
    project_id: str,
    node_uuid: str,
) -> Dict[str, Any]:
    # prepare database with default configuration
    json_configuration = default_configuration_file.read_text()
    create_pipeline(project_id)
    config_dict = _set_configuration(
        create_task, project_id, node_uuid, json_configuration
    )
    return config_dict


@pytest.fixture()
def create_node_link() -> Callable[[str], Dict[str, str]]:
    def _create(key: str) -> Dict[str, str]:
        return {"nodeUuid": "TEST_NODE_UUID", "output": key}

    return _create


@pytest.fixture()
def create_store_link(
    bucket: str,  # packages/pytest-simcore/src/pytest_simcore/minio_service.py
    create_valid_file_uuid: Callable,
    s3_simcore_location: str,
    user_id: int,
    project_id: str,
    node_uuid: str,
    storage_service: URL,  # packages/pytest-simcore/src/pytest_simcore/simcore_storage_service.py
) -> Callable[[Path], Dict[str, str]]:
    async def _create(file_path: Path) -> Dict[str, str]:
        file_path = Path(file_path)
        assert file_path.exists()

        file_id = create_valid_file_uuid(file_path)
        async with ClientSession() as session:
            async with session.put(
                f"{storage_service}/v0/locations/{s3_simcore_location}/files/{quote_plus(file_id)}",
                params={"user_id": f"{user_id}"},
            ) as resp:
                resp.raise_for_status()
                presigned_link_enveloped = await resp.json()
            link = presigned_link_enveloped.get("data", {}).get("link")
            assert link is not None

            # Upload using the link
            extra_hdr = {
                "Content-Length": f"{file_path.stat().st_size}",
                "Content-Type": "application/binary",
            }
            async with session.put(
                link, data=file_path.read_bytes(), headers=extra_hdr
            ) as resp:
                resp.raise_for_status()

        # FIXME: that at this point, S3 and pg have some data that is NOT cleaned up
        return {"store": s3_simcore_location, "path": file_id}

    return _create


@pytest.fixture(scope="function")
def create_special_configuration(
    node_ports_config: None,
    bucket: str,
    create_pipeline: Callable[[str], str],
    create_task: Callable[..., str],
    empty_configuration_file: Path,
    project_id: str,
    node_uuid: str,
) -> Callable:
    def _create(
        inputs: List[Tuple[str, str, Any]] = None,
        outputs: List[Tuple[str, str, Any]] = None,
        project_id: str = project_id,
        node_id: str = node_uuid,
    ) -> Tuple[Dict, str, str]:
        config_dict = json.loads(empty_configuration_file.read_text())
        _assign_config(config_dict, "inputs", inputs if inputs else [])
        _assign_config(config_dict, "outputs", outputs if outputs else [])
        project_id = create_pipeline(project_id)
        config_dict = _set_configuration(
            create_task, project_id, node_id, json.dumps(config_dict)
        )
        return config_dict, project_id, node_uuid

    return _create


@pytest.fixture(scope="function")
def create_2nodes_configuration(
    node_ports_config: None,
    bucket: str,
    create_pipeline: Callable[[str], str],
    create_task: Callable[..., str],
    empty_configuration_file: Path,
) -> Callable:
    def _create(
        prev_node_inputs: List[Tuple[str, str, Any]],
        prev_node_outputs: List[Tuple[str, str, Any]],
        inputs: List[Tuple[str, str, Any]],
        outputs: List[Tuple[str, str, Any]],
        project_id: str,
        previous_node_id: str,
        node_id: str,
    ) -> Tuple[Dict, str, str]:
        create_pipeline(project_id)

        # create previous node
        previous_config_dict = json.loads(empty_configuration_file.read_text())
        _assign_config(
            previous_config_dict, "inputs", prev_node_inputs if prev_node_inputs else []
        )
        _assign_config(
            previous_config_dict,
            "outputs",
            prev_node_outputs if prev_node_outputs else [],
        )
        previous_config_dict = _set_configuration(
            create_task,
            project_id,
            previous_node_id,
            json.dumps(previous_config_dict),
        )

        # create current node
        config_dict = json.loads(empty_configuration_file.read_text())
        _assign_config(config_dict, "inputs", inputs if inputs else [])
        _assign_config(config_dict, "outputs", outputs if outputs else [])
        # configure links if necessary
        str_config = json.dumps(config_dict)
        str_config = str_config.replace("TEST_NODE_UUID", previous_node_id)
        config_dict = _set_configuration(create_task, project_id, node_id, str_config)
        return config_dict, project_id, node_id

    return _create


@pytest.fixture
def create_pipeline(postgres_db: sa.engine.Engine) -> Callable[[str], str]:
    created_pipeline_ids: List[str] = []

    def _create(project_id: str) -> str:
        with postgres_db.connect() as conn:
            result = conn.execute(
                comp_pipeline.insert()  # pylint: disable=no-value-for-parameter
                .values(project_id=project_id)
                .returning(comp_pipeline.c.project_id)
            )
            new_pipeline_id = result.first()[comp_pipeline.c.project_id]
        created_pipeline_ids.append(f"{new_pipeline_id}")
        return new_pipeline_id

    yield _create

    # cleanup
    with postgres_db.connect() as conn:
        conn.execute(
            comp_pipeline.delete().where(  # pylint: disable=no-value-for-parameter
                comp_pipeline.c.project_id.in_(created_pipeline_ids)
            )
        )


@pytest.fixture
def create_task(postgres_db: sa.engine.Engine) -> Callable[..., str]:
    created_task_ids: List[int] = []

    def _create(project_id: str, node_uuid: str, **overrides) -> str:
        task_config = {
            "project_id": project_id,
            "node_id": node_uuid,
        }
        task_config.update(**overrides)
        with postgres_db.connect() as conn:
            result = conn.execute(
                comp_tasks.insert()  # pylint: disable=no-value-for-parameter
                .values(**task_config)
                .returning(comp_tasks.c.task_id)
            )
            new_task_id = result.first()[comp_tasks.c.task_id]
        created_task_ids.append(new_task_id)
        return node_uuid

    yield _create

    # cleanup
    with postgres_db.connect() as conn:
        conn.execute(
            comp_tasks.delete().where(  # pylint: disable=no-value-for-parameter
                comp_tasks.c.task_id.in_(created_task_ids)
            )
        )


def _set_configuration(
    task: Callable[..., str],
    project_id: str,
    node_id: str,
    json_configuration: str,
) -> Dict[str, Any]:
    json_configuration = json_configuration.replace("SIMCORE_NODE_UUID", str(node_id))
    configuration = json.loads(json_configuration)
    task(project_id, node_id, **configuration)
    return configuration


def _assign_config(
    config_dict: dict, port_type: str, entries: List[Tuple[str, str, Any]]
):
    if entries is None:
        return
    for entry in entries:
        config_dict["schema"][port_type].update(
            {
                entry[0]: {
                    "label": "some label",
                    "description": "some description",
                    "displayOrder": 2,
                    "type": entry[1],
                }
            }
        )
        if not entry[2] is None:
            config_dict[port_type].update({entry[0]: entry[2]})


@pytest.fixture
async def r_clone_settings_factory(
    minio_config: Dict[str, Any], storage_service: URL
) -> Awaitable[RCloneSettings]:
    async def _factory() -> RCloneSettings:
        client = minio_config["client"]
        settings = RCloneSettings.parse_obj(
            dict(
                R_CLONE_S3=dict(
                    S3_ENDPOINT=client["endpoint"],
                    S3_ACCESS_KEY=client["access_key"],
                    S3_SECRET_KEY=client["secret_key"],
                    S3_BUCKET_NAME=minio_config["bucket_name"],
                    S3_SECURE=client["secure"],
                ),
                R_CLONE_PROVIDER=S3Provider.MINIO,
            )
        )
        if not await is_r_clone_available(settings):
            pytest.skip("rclone not installed")

        return settings

    return _factory()


@pytest.fixture
async def r_clone_settings(
    r_clone_settings_factory: Awaitable[RCloneSettings],
) -> RCloneSettings:
    return await r_clone_settings_factory
