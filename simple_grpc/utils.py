import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from google.protobuf import json_format
from google.protobuf.message import Message


def module_from_file(path: Path, name: str) -> ModuleType:
    """
    Load one Python module from a file path.

    Args:
        path: File to load, e.g. ``.../route_guide_pb2.py``.
        name: Module name under which the file is executed.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore

    return module


def find_service(
    mods: dict[str, tuple[ModuleType, ModuleType]], service: str
) -> tuple[ModuleType, ModuleType]:
    """
    Locate which compiled proto defines a service.

    Args:
        mods: Mapping returned by :meth:`_load_generated`.
        service: Service name to search for.

    Returns:
        The ``(pb2, pb2_grpc)`` modules containing the service.

    Raises:
        ValueError: If no compiled proto defines the service.
    """
    for pb2, pb2_grpc in mods.values():
        if hasattr(pb2_grpc, f"{service}Servicer"):
            return pb2, pb2_grpc

    available = [name for pb2, _ in mods.values() for name in pb2.DESCRIPTOR.services_by_name]
    raise ValueError(f"service {service!r} not found in compiled protos. Available: {available}")


def reply(value: dict[str, Any] | Message | None, response_cls: type[Message]) -> Message | None:
    """
    Convert one handler result into the response message.

    Args:
        value: A message (passthrough), a dict serialized via
            ``json_format``, or ``None`` for an empty response.
        response_cls: Generated message class for the response type.

    Returns:
        The response ``Message``.

    Raises:
        TypeError: If the value is neither a dict, a ``Message``, nor ``None``.
    """
    if isinstance(value, Message) or value is None:
        return value
    if isinstance(value, dict):
        return json_format.ParseDict(value, response_cls())

    raise TypeError(f"handler returned {type(value).__name__}, expected dict or Message")
