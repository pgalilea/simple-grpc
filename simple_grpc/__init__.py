from collections.abc import Iterator

from google.protobuf.message import Message
from grpc import ServicerContext as Context, StatusCode as status

from .applications import SimpleGRPC
from .params import Depends


Request = Message  # Requests are application-defined proto message classes, handled opaquely,
RequestIterator = Iterator[Message]  # so Any is the honest annotation.

# __all__ = ["Context", "Depends", "Request", "RequestIterator", "SimpleGRPC", "status"]
