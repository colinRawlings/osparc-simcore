import json
import logging
from asyncio import Lock
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional
from uuid import UUID

from models_library.projects_nodes_io import NodeID
from models_library.service_settings_labels import (
    DynamicSidecarServiceLabels,
    PathMappingsLabel,
    SimcoreServiceLabels,
)
from models_library.services_resources import ServiceResourcesDict
from pydantic import BaseModel, Extra, Field, PositiveInt, PrivateAttr, constr

from ..constants import (
    DYNAMIC_PROXY_SERVICE_PREFIX,
    DYNAMIC_SIDECAR_SCHEDULER_DATA_LABEL,
    DYNAMIC_SIDECAR_SERVICE_PREFIX,
    REGEX_DY_SERVICE_PROXY,
    REGEX_DY_SERVICE_SIDECAR,
)
from .service import CommonServiceDetails

TEMPORARY_PORT_NUMBER = 65_534

MAX_ALLOWED_SERVICE_NAME_LENGTH: int = 63

DockerId = constr(max_length=25, regex=r"[A-Za-z0-9]{25}")
ServiceId = DockerId
NetworkId = DockerId

logger = logging.getLogger()


def _strip_service_name(service_name: str) -> str:
    """returns: the maximum allowed service name in docker swarm"""
    return service_name[:MAX_ALLOWED_SERVICE_NAME_LENGTH]


def assemble_service_name(service_prefix: str, node_uuid: NodeID) -> str:
    return _strip_service_name("_".join([service_prefix, str(node_uuid)]))


class DynamicSidecarStatus(str, Enum):
    OK = "ok"  # running as expected
    FAILING = "failing"  # requests to the sidecar API are failing service should be cosnidered as unavailable


class Status(BaseModel):
    """Generated from data from docker container inspect API"""

    current: DynamicSidecarStatus = Field(..., description="status of the service")
    info: str = Field(..., description="additional information for the user")

    def _update(self, new_status: DynamicSidecarStatus, new_info: str) -> None:
        self.current = new_status
        self.info = new_info

    def update_ok_status(self, info: str) -> None:
        self._update(DynamicSidecarStatus.OK, info)

    def update_failing_status(self, info: str) -> None:
        self._update(DynamicSidecarStatus.FAILING, info)

    def __eq__(self, other: "Status") -> bool:
        return self.current == other.current and self.info == other.info

    @classmethod
    def create_as_initially_ok(cls) -> "Status":
        # the service is initially ok when started
        initial_state = cls(current=DynamicSidecarStatus.OK, info="")
        return initial_state


class DockerStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    RESTARTING = "restarting"
    REMOVING = "removing"
    EXITED = "exited"
    DEAD = "dead"


class DockerContainerInspect(BaseModel):
    status: DockerStatus = Field(
        ...,
        scription="status of the underlying container",
    )
    name: str = Field(..., description="docker name of the container")
    id: str = Field(..., description="docker id of the container")

    @classmethod
    def from_container(cls, container: Dict[str, Any]) -> "DockerContainerInspect":
        return cls(
            status=DockerStatus(container["State"]["Status"]),
            name=container["Name"],
            id=container["Id"],
        )


class ServiceRemovalState(BaseModel):
    can_remove: bool = Field(
        False,
        description="when True, marks the service as ready to be removed",
    )
    can_save: Optional[bool] = Field(
        None,
        description="when True, saves the internal state and upload outputs of the service",
    )
    was_removed: bool = Field(
        False,
        description=(
            "Will be True when the removal finished. Used primarily "
            "to cancel retrying long running operations."
        ),
    )

    def mark_to_remove(self, can_save: Optional[bool]) -> None:
        self.can_remove = True
        self.can_save = can_save

    def mark_removed(self) -> None:
        self.can_remove = False
        self.was_removed = True


class DynamicSidecar(BaseModel):
    status: Status = Field(
        Status.create_as_initially_ok(),
        description="status of the service sidecar also with additional information",
    )

    hostname: str = Field(..., description="docker hostname for this service")

    port: PositiveInt = Field(8000, description="dynamic-sidecar port")

    is_available: bool = Field(
        False,
        scription=(
            "is True while the health check on the dynamic-sidecar is responding. "
            "Meaning that the dynamic-sidecar is reachable and can accept requests"
        ),
    )

    @property
    def compose_spec_submitted(self) -> bool:
        """
        If the director-v2 is rebooted was_compose_spec_submitted is False
        If the compose-spec is submitted it can be safely assumed that the
        containers_inspect contains some elements.
        """
        return self.was_compose_spec_submitted or len(self.containers_inspect) > 0

    was_compose_spec_submitted: bool = Field(
        False,
        description="if the docker-compose spec was already submitted this fields is True",
    )

    containers_inspect: List[DockerContainerInspect] = Field(
        [],
        scription="docker inspect results from all the container ran at regular intervals",
    )

    was_dynamic_sidecar_started: bool = False
    were_services_created: bool = Field(
        False,
        description=(
            "when True no longer will the Docker api "
            "be used to check if the services were started"
        ),
    )
    is_project_network_attached: bool = Field(
        False,
        description=(
            "When True, all containers were in running state and project "
            "networks were attached. Waiting for the container sto be in "
            "running state guarantees all containers have been created"
        ),
    )

    service_environment_prepared: bool = Field(
        False,
        description=(
            "True when the environment setup required by the "
            "dynamic-sidecars created services was completed."
            "Example: nodeports data downloaded, globally "
            "shared service data fetched, etc.."
        ),
    )

    service_removal_state: ServiceRemovalState = Field(
        default_factory=ServiceRemovalState,
        description=(
            "stores information used during service removal "
            "from the dynamic-sidecar scheduler"
        ),
    )

    # below had already been validated and
    # used only to start the proxy
    dynamic_sidecar_id: Optional[ServiceId] = Field(
        None, description="returned by the docker engine; used for starting the proxy"
    )
    dynamic_sidecar_network_id: Optional[NetworkId] = Field(
        None, description="returned by the docker engine; used for starting the proxy"
    )
    swarm_network_id: Optional[NetworkId] = Field(
        None, description="returned by the docker engine; used for starting the proxy"
    )
    swarm_network_name: Optional[str] = Field(
        None, description="used for starting the proxy"
    )

    @property
    def can_save_state(self) -> bool:
        """
        Keeps track of the current state of the application, if it was starte successfully
        the state of the service can be saved when stopping the service
        """
        # TODO: implement when adding save status hooks
        return False

    # consider adding containers for healthchecks but this is more difficult and it depends on each service

    @property
    def endpoint(self):
        """endpoint where all the services are exposed"""
        return f"http://{self.hostname}:{self.port}"

    @property
    def are_containers_ready(self) -> bool:
        """returns: True if all containers are in running state"""
        return all(
            docker_container_inspect.status == DockerStatus.RUNNING
            for docker_container_inspect in self.containers_inspect
        )


class DynamicSidecarNames(BaseModel):
    """
    Service naming schema:
    NOTE: name is max 63 characters
    dy-sidecar_4dde07ea-73be-4c44-845a-89479d1556cf
    dy-proxy_4dde07ea-73be-4c44-845a-89479d1556cf

    dynamic sidecar structure
    0. a network is created: dy-sidecar_4dde07ea-73be-4c44-845a-89479d1556cf
    1. a dynamic-sidecar is started: dy-sidecar_4dde07ea-73be-4c44-845a-89479d1556cf
    a traefik instance: dy-proxy_4dde07ea-73be-4c44-845a-89479d1556cf
    """

    service_name_dynamic_sidecar: str = Field(
        ...,
        regex=REGEX_DY_SERVICE_SIDECAR,
        max_length=MAX_ALLOWED_SERVICE_NAME_LENGTH,
        description="unique name of the dynamic-sidecar service",
    )
    proxy_service_name: str = Field(
        ...,
        regex=REGEX_DY_SERVICE_PROXY,
        max_length=MAX_ALLOWED_SERVICE_NAME_LENGTH,
        description="name of the proxy for the dynamic-sidecar",
    )

    simcore_traefik_zone: str = Field(
        ...,
        regex=REGEX_DY_SERVICE_SIDECAR,
        description="unique name for the traefik constraints",
    )
    dynamic_sidecar_network_name: str = Field(
        ...,
        regex=REGEX_DY_SERVICE_SIDECAR,
        description="based on the node_id and project_id",
    )

    @classmethod
    def make(cls, node_uuid: UUID) -> "DynamicSidecarNames":
        return cls(
            service_name_dynamic_sidecar=assemble_service_name(
                DYNAMIC_SIDECAR_SERVICE_PREFIX, node_uuid
            ),
            proxy_service_name=assemble_service_name(
                DYNAMIC_PROXY_SERVICE_PREFIX, node_uuid
            ),
            simcore_traefik_zone=f"{DYNAMIC_SIDECAR_SERVICE_PREFIX}_{node_uuid}",
            dynamic_sidecar_network_name=f"{DYNAMIC_SIDECAR_SERVICE_PREFIX}_{node_uuid}",
        )


class SchedulerData(CommonServiceDetails, DynamicSidecarServiceLabels):
    service_name: str = Field(
        ..., description="Name of the current dynamic-sidecar being observed"
    )

    dynamic_sidecar: DynamicSidecar = Field(
        ...,
        description="stores information fetched from the dynamic-sidecar",
    )

    paths_mapping: PathMappingsLabel  # overwrites in DynamicSidecarServiceLabels

    dynamic_sidecar_network_name: str = Field(
        ...,
        description="overlay network biding the proxy to the container spaned by the dynamic-sidecar",
    )

    simcore_traefik_zone: str = Field(
        ...,
        description="required for Traefik to correctly route requests to the spawned container",
    )

    service_port: PositiveInt = Field(
        TEMPORARY_PORT_NUMBER,
        description=(
            "port where the service is exposed defined by the service; "
            "NOTE: temporary default because it will be changed once the service "
            "is started, this value is fetched from the service start spec"
        ),
    )

    service_resources: ServiceResourcesDict = Field(
        ..., description="service resources used to enforce limits"
    )

    # Below values are used only once and then are nto required, thus optional
    # after the service is picked up by the scheduler after a reboot these are not required
    # and can be set to None
    request_dns: Optional[str] = Field(
        None, description="used when configuring the CORS options on the proxy"
    )
    request_scheme: Optional[str] = Field(
        None, description="used when configuring the CORS options on the proxy"
    )
    proxy_service_name: Optional[str] = Field(
        None, description="service name given to the proxy"
    )

    @classmethod
    def from_http_request(
        # pylint: disable=too-many-arguments
        cls,
        service: "DynamicServiceCreate",
        simcore_service_labels: SimcoreServiceLabels,
        port: Optional[int],
        request_dns: str = None,
        request_scheme: str = None,
    ) -> "SchedulerData":
        dynamic_sidecar_names = DynamicSidecarNames.make(service.node_uuid)

        obj_dict = dict(
            service_name=dynamic_sidecar_names.service_name_dynamic_sidecar,
            node_uuid=service.node_uuid,
            project_id=service.project_id,
            user_id=service.user_id,
            key=service.key,
            version=service.version,
            service_resources=service.service_resources,
            paths_mapping=simcore_service_labels.paths_mapping,
            compose_spec=json.dumps(simcore_service_labels.compose_spec),
            container_http_entry=simcore_service_labels.container_http_entry,
            restart_policy=simcore_service_labels.restart_policy,
            dynamic_sidecar_network_name=dynamic_sidecar_names.dynamic_sidecar_network_name,
            simcore_traefik_zone=dynamic_sidecar_names.simcore_traefik_zone,
            request_dns=request_dns,
            request_scheme=request_scheme,
            proxy_service_name=dynamic_sidecar_names.proxy_service_name,
            dynamic_sidecar=dict(
                hostname=dynamic_sidecar_names.service_name_dynamic_sidecar,
                port=port,
            ),
        )
        return cls.parse_obj(obj_dict)

    @classmethod
    def from_service_inspect(
        cls, service_inspect: Mapping[str, Any]
    ) -> "SchedulerData":
        labels = service_inspect["Spec"]["Labels"]
        return cls.parse_raw(labels[DYNAMIC_SIDECAR_SCHEDULER_DATA_LABEL])

    def as_label_data(self) -> str:
        # compose_spec needs to be json encoded before encoding it to json
        # and storing it in the label
        return self.copy(
            update={"compose_spec": json.dumps(self.compose_spec)}, deep=True
        ).json()

    class Config:
        extra = Extra.allow
        allow_population_by_field_name = True


class AsyncResourceLock(BaseModel):
    _lock: Lock = PrivateAttr()
    _is_locked = PrivateAttr()

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._lock = Lock()

    @classmethod
    def from_is_locked(cls, is_locked: bool) -> "AsyncResourceLock":
        instance = cls()
        instance._is_locked = is_locked
        return instance

    async def mark_as_locked_if_unlocked(self) -> bool:
        """
        If the resource is currently not in used it will mark it as in use.

        returns: True if it succeeds otherwise False
        """
        async with self._lock:
            if not self._is_locked:
                self._is_locked = True
                return True

        return False

    async def unlock_resource(self) -> None:
        """Marks the resource as unlocked"""
        async with self._lock:
            self._is_locked = False


class LockWithSchedulerData(BaseModel):
    # locking is required to guarantee the scheduling will not be triggered
    # twice in a row for the service
    resource_lock: AsyncResourceLock = Field(
        ...,
        description=(
            "needed to exclude the service from a scheduling cycle while another "
            "scheduling cycle is already running"
        ),
    )

    scheduler_data: SchedulerData = Field(
        ..., description="required data used to schedule the dynamic-sidecar"
    )
