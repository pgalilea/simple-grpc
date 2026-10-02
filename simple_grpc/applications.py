from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import inspect
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

from google.protobuf import json_format, message_factory
from google.protobuf.message import Message
import grpc
from grpc_tools import protoc

from .models import ProtoConfig


class SimpleGRPC:
    """FastAPI-style gRPC server: register plain handlers, we compile and wire.

    Handlers are attached with :meth:`rpc` and may declare any subset of the
    injectable parameters (by name): ``request`` (the request message, or the
    request iterator on streaming RPCs), ``request_iterator`` (alias, same
    object) and ``context``.
    """

    #: Parameter names a handler may declare for dependency injection.
    INJECTABLE = ("request", "request_iterator", "context")

    def __init__(
        self,
        thread_pool: ThreadPoolExecutor | None = None,
        proto_path: str | Path = "protos",
        proto_files: list[str] | None = None,
        service_path: str | Path = "grpc_services",
        port: int = 50051,
    ) -> None:
        """Store the compile config; handlers attach later via :meth:`rpc`.

        Args:
            thread_pool: Executor used to serve requests. Defaults to a new
                ``ThreadPoolExecutor``.
            proto_path: Directory containing ``.proto`` files.
            proto_files: Proto files to compile; defaults to all ``*.proto``
                in ``proto_path``.
            service_path: Output directory for the generated ``*_pb2*.py``.
            port: Port the server binds with ``dev()``/``run()``.
        """
        self.thread_pool = thread_pool or ThreadPoolExecutor()
        self.proto_config = self.config_proto_files(
            proto_path=proto_path,
            proto_files=proto_files,
            service_path=service_path,
        )
        self.port = port
        self._handlers: dict[str, dict[str, Callable]] = {}

    def config_proto_files(
        self,
        proto_path: str | Path,
        proto_files: list[str] | None,
        service_path: str | Path,
    ) -> ProtoConfig:
        """Build the compile config.

        Args:
            proto_path: Directory containing ``.proto`` files.
            proto_files: Proto files to compile, or ``None`` to discover all.
            service_path: Output directory for generated modules.

        Returns:
            The resolved :class:`ProtoConfig`.
        """
        return ProtoConfig(
            proto_path=proto_path,
            proto_files=proto_files,
            service_path=service_path,
        )

    def rpc(self, service: str, method: str) -> Callable[[Callable], Callable]:
        """Decorate a function as the handler for one RPC method.

        Args:
            service: Proto service name, e.g. ``"RouteGuide"``.
            method: RPC method name within the service, e.g. ``"GetFeature"``.

        Returns:
            A decorator that registers the function and returns it unchanged.
        """

        def decorator(func: Callable) -> Callable:
            """Register ``func`` under ``(service, method)``.

            Args:
                func: The decorated handler function.

            Returns:
                The same function, unchanged.
            """
            self._handlers.setdefault(service, {})[method] = func
            return func

        return decorator

    def dev(self) -> None:
        """Compile the protos, wire the handlers, and serve until killed.

        Raises:
            RuntimeError: If ``protoc`` exits with a non-zero status.
        """
        self._start()

    def run(self) -> None:
        """Serve like :meth:`dev`; compile-skipping is deferred until needed.

        Raises:
            RuntimeError: If ``protoc`` exits with a non-zero status.
        """
        self._start()

    def _start(self) -> None:
        """Compile, load generated modules, register services, and serve."""
        self._compile()
        mods = self._load_generated()
        server = grpc.server(self.thread_pool)
        self._register_services(mods, server)
        server.add_insecure_port(f"[::]:{self.port}")
        server.start()
        server.wait_for_termination()

    def _compile(self) -> None:
        """Run protoc against the configured proto files.

        Raises:
            RuntimeError: If ``protoc`` exits with a non-zero status.
        """
        if not self.proto_config.service_path.exists():
            raise ValueError(f"service_path '{self.proto_config.service_path}' does not exist.")
        if protoc.main(self.proto_config.to_compiler_args()) != 0:
            raise RuntimeError(f"protoc failed for {self.proto_config.proto_files}")

    def _load_generated(self) -> dict[str, tuple[ModuleType, ModuleType]]:
        """Import the freshly compiled ``*_pb2``/``*_pb2_grpc`` modules.

        Returns:
            Mapping of proto stem to ``(pb2_module, pb2_grpc_module)``.
        """
        sp = self.proto_config.service_path
        mods: dict[str, tuple[ModuleType, ModuleType]] = {}

        for pf in self.proto_config.proto_files:
            name = Path(pf).stem
            pb2 = self._module_from_file(sp / f"{name}_pb2.py", f"{name}_pb2")
            # _pb2_grpc does a bare `import <name>_pb2`, so seed sys.modules;
            # pop first so a recompile in the dev loop never serves stale code.
            sys.modules.pop(f"{name}_pb2", None)
            sys.modules[f"{name}_pb2"] = pb2
            pb2_grpc = self._module_from_file(sp / f"{name}_pb2_grpc.py", f"{name}_pb2_grpc")
            mods[name] = (pb2, pb2_grpc)

        return mods

    @staticmethod
    def _module_from_file(path: Path, name: str) -> ModuleType:
        """Load one Python module from a file path.

        Args:
            path: File to load, e.g. ``.../route_guide_pb2.py``.
            name: Module name under which the file is executed.

        Returns:
            The loaded module.
        """
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _register_services(
        self, mods: dict[str, tuple[ModuleType, ModuleType]], server: grpc.Server
    ) -> None:
        """Attach a dispatching servicer to the server for each service.

        Args:
            mods: Mapping returned by :meth:`_load_generated`.
            server: The server receiving the servicers.
        """
        for service in self._handlers:
            pb2, pb2_grpc = self._find_service(mods, service)
            servicer = self._build_servicer(service, pb2, pb2_grpc)
            getattr(pb2_grpc, f"add_{service}Servicer_to_server")(servicer, server)

    @staticmethod
    def _find_service(
        mods: dict[str, tuple[ModuleType, ModuleType]], service: str
    ) -> tuple[ModuleType, ModuleType]:
        """Locate which compiled proto defines a service.

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
        raise ValueError(
            f"service {service!r} not found in compiled protos. Available: {available}"
        )

    def _build_servicer(self, service: str, pb2: ModuleType, pb2_grpc: ModuleType) -> Any:
        """Subclass the generated servicer so each RPC dispatches to its handler.

        Args:
            service: Service name.
            pb2: Generated messages module.
            pb2_grpc: Generated service classes module.

        Returns:
            An instance of the generated servicer subclass.

        Raises:
            ValueError: If a registered handler names an unknown RPC method.
        """
        base = getattr(pb2_grpc, f"{service}Servicer")
        handlers = self._handlers[service]
        unknown = set(handlers) - set(vars(base))
        if unknown:
            available = list(pb2.DESCRIPTOR.services_by_name[service].methods_by_name)
            raise ValueError(
                f"service {service!r} has no RPC methods: {sorted(unknown)}. Available: {available}"
            )

        methods = pb2.DESCRIPTOR.services_by_name[service].methods_by_name
        overrides = {
            name: self._wrap_handler(
                handler, message_factory.GetMessageClass(methods[name].output_type)
            )
            for name, handler in handlers.items()
        }
        return type("SimpleGRPCServicer", (base,), overrides)()

    @classmethod
    def _wrap_handler(cls, handler: Callable, response_cls: type[Message]) -> Callable:
        """Wrap a handler for gRPC dispatch, injecting only declared parameters.

        Args:
            handler: The user-registered handler.
            response_cls: Generated message class for the RPC's response type.

        Returns:
            The servicer method that gRPC invokes for this RPC.

        Raises:
            ValueError: If the handler declares a non-injectable parameter.
        """
        params = set(inspect.signature(handler).parameters)
        unknown = params - set(cls.INJECTABLE)
        if unknown:
            raise ValueError(
                f"handler {handler.__name__!r} declares unregistrable parameter(s) "
                f"{sorted(unknown)}; injectable names: {list(cls.INJECTABLE)}"
            )

        def method(self: Any, request: Any, context: grpc.ServicerContext) -> Any:
            """Dispatch to the user handler with only its declared parameters.

            Args:
                self: The servicer instance (unused, required by gRPC).
                request: Request message, or the request iterator on streams.
                context: gRPC servicer context.

            Returns:
                Whatever the handler returns, serialized for the wire.
            """
            kwargs: dict[str, Any] = {}
            if "request" in params:
                kwargs["request"] = request
            if "request_iterator" in params:
                kwargs["request_iterator"] = request  # same object on streaming RPCs
            if "context" in params:
                kwargs["context"] = context
            result = handler(**kwargs)
            if isinstance(result, (Message, dict)) or result is None:
                return cls._reply(result, response_cls)
            return Iterator(cls._reply(item, response_cls) for item in result)

        return method

    @staticmethod
    def _reply(value: dict[str, Any] | Message | None, response_cls: type[Message]) -> Message:
        """Convert one handler result into the response message.

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
