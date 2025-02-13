""" Nodes (services) in a project implemented with python functions (denoted meta-function nodes)


So far, nodes in a project could be front-end, computational or dynamic. The first
was fully implemented in the web-client (e.g. file-picker) while the two last were implemented
independently as black-boxes (i.e. basically only i/o description known) inside of containers. Here
we start a new type of nodes declared as "front-end" but implemented as python functions in the backend.

Meta-function nodes are evaluated in the backend in the pre-run stage of a meta-project run.

An example of meta-function is the "Integers Iterator" node.
"""

from copy import deepcopy

from models_library.function_services_catalog import catalog, is_iterator_service
from models_library.projects_nodes import Node

# META-FUNCTIONS ---------------------------------------------------
assert catalog  # nosec

# UTILS ---------------------------------------------------------------


def create_param_node_from_iterator_with_outputs(iterator_node: Node) -> Node:
    """
    Converts an iterator_node with outputs (i.e. evaluated) to a parameter-node
    that represents a constant value.
    """
    #
    # TODO: this MUST be implemented with a more sophisticated mechanism
    # that can replace any node with equivalent param-node with outputs
    #

    assert is_iterator_service(iterator_node.key)  # nosec
    assert iterator_node.version == "1.0.0"  # nosec

    return Node(
        key="simcore/services/frontend/parameter/integer",
        version="1.0.0",
        label=iterator_node.label,
        inputs={},
        inputNodes=[],
        thumbnail="",  # TODO: hack due to issue in projects json-schema
        outputs=deepcopy(iterator_node.outputs),
    )
