from collections.abc import Iterator
from typing import Any

from grpc import ServicerContext as Context, StatusCode as status

from .applications import SimpleGRPC
from .params import Depends


Request = Any  # Requests are application-defined proto message classes, handled opaquely,
RequestIterator = Iterator[Any]  # so Any is the honest annotation.

# __all__ = ["Context", "Depends", "Request", "RequestIterator", "SimpleGRPC", "status"]
