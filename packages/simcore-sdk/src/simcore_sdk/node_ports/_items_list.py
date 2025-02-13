import logging
from collections.abc import Sequence

from ..node_ports_common import exceptions
from ._data_items_list import DataItemsList
from ._item import Item
from ._schema_items_list import SchemaItemsList

log = logging.getLogger(__name__)

# pylint: disable=too-many-instance-attributes
class ItemsList(Sequence):
    def __init__(
        self,
        user_id: int,
        project_id: str,
        node_uuid: str,
        schemas: SchemaItemsList,
        payloads: DataItemsList,
        change_cb=None,
        get_node_from_node_uuid_cb=None,
    ):
        self._user_id = user_id
        self._project_id = project_id
        self._node_uuid = node_uuid

        self._schemas = schemas
        self._payloads = payloads

        self._change_notifier = change_cb
        self._get_node_from_node_uuid_cb = get_node_from_node_uuid_cb

    def __getitem__(self, key) -> Item:
        schema = self._schemas[key]
        payload = None
        try:
            payload = self._payloads[schema.key]
        except (exceptions.InvalidKeyError, exceptions.UnboundPortError):
            # there is no payload
            payload = None

        item = Item(self._user_id, self._project_id, self._node_uuid, schema, payload)
        item.new_data_cb = self._item_value_updated_cb
        item.get_node_from_uuid_cb = self.get_node_from_node_uuid_cb
        return item

    def __len__(self):
        return len(self._schemas)

    @property
    def change_notifier(self):
        """Callback function to be set if client code wants notifications when
        an item is modified or replaced
        """
        return self._change_notifier

    @change_notifier.setter
    def change_notifier(self, value):
        self._change_notifier = value

    @property
    def get_node_from_node_uuid_cb(self):
        return self._get_node_from_node_uuid_cb

    @get_node_from_node_uuid_cb.setter
    def get_node_from_node_uuid_cb(self, value):
        self._get_node_from_node_uuid_cb = value

    async def _item_value_updated_cb(self, new_data_item):
        # a new item shall replace the current one
        self.__replace_item(new_data_item)
        await self._notify_client()

    def __replace_item(self, new_data_item):
        self._payloads[new_data_item.key] = new_data_item

    async def _notify_client(self):
        if self.change_notifier and callable(self.change_notifier):
            await self.change_notifier()  # pylint: disable=not-callable
