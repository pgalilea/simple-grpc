from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
import inspect
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Unpack

from google.protobuf import message_factory
from google.protobuf.message import Message
import grpc
from grpc_tools import protoc

from simple_grpc.exceptions import GRPCException
from simple_grpc.models import ProtoConfig, ServerCredentials, ServerOptions
from simple_grpc.params import Depends, hints, noop, param_dependency
import simple_grpc.utils as utils


class SimpleGRPC:
    """
    `SimpleGRPC` app class, the main entrypoint to use SimpleGRPC.

    Handlers are attached with :meth:`rpc` and may declare any subset of the
    injectable parameters (by name): ``request`` (the request message, or the
    request iterator on streaming RPCs), ``request_iterator`` (alias, same
    object) and ``context``.

    Handlers may also declare dependencies with
    ``Depends(...)`` parameter defaults. Dependencies (app-wide ones passed to
    the constructor, or signature ones declared on the handler) are
    plain functions resolved automatically at call time, yielding pre/post
    interceptor-like behavior for generator dependencies.
    """

    # Parameter names a handler may declare for dependency injection.
    INJECTABLE = ("request", "request_iterator", "context")

    def __init__(
        self,
        thread_pool: ThreadPoolExecutor | None = None,
        proto_path: str | Path = "protos",
        proto_files: list[str] | None = None,
        service_path: str | Path = "grpc_services",
        port: int = 50051,
        credentials: ServerCredentials | None = None,
        dependencies: list[Depends] | None = None,
        **server_options: Unpack[ServerOptions],
    ) -> None:
        """
        Store the compile config; handlers attach later via :meth:`rpc`.

        Args:
            thread_pool: Executor used to serve requests. Defaults to a new
                ``ThreadPoolExecutor``.
            proto_path: Directory containing ``.proto`` files.
            proto_files: Proto files to compile; defaults to all ``*.proto``
                in ``proto_path``.
            service_path: Output directory for the generated ``*_pb2*.py``.
            port: Port the server binds with ``dev()``/``run()``.
            credentials: TLS credentials; each PEM field takes a ``Path`` or
                the content itself. Given, ``_start`` binds with
                ``add_secure_port`` (and ``ssl_server_credentials``); absent,
                the port stays insecure.
            dependencies: App-wide ``Depends`` resolved before every RPC,
                applied automatically to all handlers. Generator dependencies
                wrap each call (pre/`yield`/post).
            **server_options: Forwarded verbatim to ``grpc.server()`` at
                startup (``options``, ``compression``, ``interceptors``,
                ``maximum_concurrent_rpcs``, ``xds``, ...). Passing
                ``thread_pool`` here raises ``TypeError``. ``handlers``
                bypasses this library's servicer registration and is only
                useful for exotic ``GenericRpcHandler`` setups.
        """
        self.thread_pool = thread_pool or ThreadPoolExecutor()
        self.proto_config = self.config_proto_files(
            proto_path=proto_path,
            proto_files=proto_files,
            service_path=service_path,
        )
        self.port = port
        self.credentials = credentials
        self._handlers: dict[str, dict[str, Callable]] = {}
        self._app_dependencies: tuple[Depends, ...] = tuple(dependencies or ())
        self._server_options: ServerOptions = server_options

    def _start(self) -> None:
        """Compile, load generated modules, register services, and serve."""

        self._compile()
        mods = self._load_generated()
        server = grpc.server(self.thread_pool, **self._server_options)
        self._register_services(mods, server)
        if self.credentials:
            server.add_secure_port(f"[::]:{self.port}", self.credentials.to_server_credentials())
        else:
            server.add_insecure_port(f"[::]:{self.port}")
        server.start()
        server.wait_for_termination()

    def _compile(self) -> None:
        """
        Run protoc against the configured proto files.

        Raises:
            RuntimeError: If ``protoc`` exits with a non-zero status.
        """
        if not self.proto_config.service_path.exists():
            raise ValueError(f"service_path '{self.proto_config.service_path}' does not exist.")
        if protoc.main(self.proto_config.to_compiler_args()) != 0:
            raise RuntimeError(f"protoc failed for {self.proto_config.proto_files}")

    def _load_generated(self) -> dict[str, tuple[ModuleType, ModuleType]]:
        """
        Import the freshly compiled ``*_pb2``/``*_pb2_grpc`` modules.

        Returns:
            Mapping of proto stem to ``(pb2_module, pb2_grpc_module)``.
        """
        sp = self.proto_config.service_path
        mods: dict[str, tuple[ModuleType, ModuleType]] = {}

        for pf in self.proto_config.proto_files:
            name = Path(pf).stem
            pb2 = utils.module_from_file(sp / f"{name}_pb2.py", f"{name}_pb2")
            # _pb2_grpc does a bare `import <name>_pb2`, so seed sys.modules;
            # pop first so a recompile in the dev loop never serves stale code.
            sys.modules.pop(f"{name}_pb2", None)
            sys.modules[f"{name}_pb2"] = pb2
            pb2_grpc = utils.module_from_file(sp / f"{name}_pb2_grpc.py", f"{name}_pb2_grpc")
            mods[name] = (pb2, pb2_grpc)

        return mods

    def _build_servicer(self, service: str, pb2: ModuleType, pb2_grpc: ModuleType) -> Any:
        """
        Subclass the generated servicer so each RPC dispatches to its handler.

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
                handler,
                message_factory.GetMessageClass(methods[name].output_type),
                self._app_dependencies,
            )
            for name, handler in handlers.items()
        }
        return type("SimpleGRPCServicer", (base,), overrides)()

    def _register_services(
        self, mods: dict[str, tuple[ModuleType, ModuleType]], server: grpc.Server
    ) -> None:
        """
        Attach a dispatching servicer to the server for each service.

        Args:
            mods: Mapping returned by :meth:`_load_generated`.
            server: The server receiving the servicers.
        """
        for service in self._handlers:
            pb2, pb2_grpc = utils.find_service(mods, service)
            servicer = self._build_servicer(service, pb2, pb2_grpc)
            getattr(pb2_grpc, f"add_{service}Servicer_to_server")(servicer, server)

    @classmethod
    def _stream(
        cls, result, response_cls: type[Message], context: grpc.ServicerContext, tails
    ) -> Iterator[Any]:
        """
        Serialize each item, mapping GRPCException to a status.

        Teardown runs after the last item, so generator
        dependencies (e.g. DB sessions) stay alive while gRPC
        consumes the stream.
        """
        try:
            for item in result:
                yield utils.reply(item, response_cls)
        except GRPCException as exc:
            context.abort(exc.code, exc.details)
        finally:
            for tail in tails:
                tail()

    @classmethod
    def _wrap_handler(
        cls,
        handler: Callable,
        response_cls: type[Message],
        dependencies: tuple[Depends, ...] = (),
    ) -> Callable:
        """
        Wrap a handler for gRPC dispatch, resolving its declared parameters.

        Injected parameters: ``request``, ``request_iterator``, ``context``
        (by name) and any parameter whose default is a ``Depends``. The
        ``dependencies`` are resolved as side-effect dependencies before the
        handler runs; generator dependencies run their remainder afterwards.

        Args:
            handler: The user-registered handler.
            response_cls: Generated message class for the RPC's response type.
            dependencies: App-wide ``Depends`` for this handler,
                resolved before (outside) the signature dependencies.

        Returns:
            The servicer method that gRPC invokes for this RPC.

        Raises:
            ValueError: If the handler or a dependency declares a
                non-injectable and non-``Depends`` parameter.
        """
        params = inspect.signature(handler).parameters
        fn_hints = hints(handler)
        unknown = sorted(
            name
            for name, param in params.items()
            if name not in cls.INJECTABLE
            and param_dependency(param, fn_hints.get(name, param.annotation)) is None
        )
        if unknown:
            raise ValueError(
                f"handler {handler.__name__!r} declares unregistrable parameter(s) "
                f"{unknown}; injectable names: {list(cls.INJECTABLE)} or "
                f"Depends(dependency) defaults"
            )
        handler_deps = [
            dep
            for param in params.values()
            for dep in [param_dependency(param, fn_hints.get(param.name, param.annotation))]
            if dep is not None
        ]

        for dep in (*handler_deps, *dependencies):
            cls._validate_dependency(dep.dependency)

        def method(self: Any, request: Any, context: grpc.ServicerContext) -> Any:
            """Dispatch to the user handler with its dependencies resolved.

            Args:
                self: The servicer instance (unused, required by gRPC).
                request: Request message, or the request iterator on streams.
                context: gRPC servicer context.

            Returns:
                Whatever the handler returns, serialized for the wire.

            Raises:
                Exception: Any exception from the handler or dependencies.
                    ``GRPCException`` subclasses abort the RPC with their
                    status code and details; other exceptions propagate for
                    gRPC to map to ``UNKNOWN`` (dependencies may also call
                    ``context.abort`` for a specific status code).
            """
            tails: list[Callable[[], None]] = []

            def resolve(fn: Callable) -> Any:
                """Resolve one dependency, queueing its post-call teardown.

                Args:
                    fn: The dependency function.

                Returns:
                    The value the dependency produced (``None`` for side
                    effect / generator-only dependencies).
                """
                value, tail = cls._resolve(fn, request, context)
                tails.append(tail)
                return value

            for dep in dependencies:
                resolve(dep.dependency)

            kwargs: dict[str, Any] = {}
            for name, param in params.items():
                dep = param_dependency(param, fn_hints.get(name, param.annotation))
                if dep is not None:
                    kwargs[name] = resolve(dep.dependency)
                elif name == "request" or name == "request_iterator":
                    kwargs[name] = request
                elif name == "context":
                    kwargs[name] = context

            streaming = False
            try:
                result = handler(**kwargs)
                if isinstance(result, (Message, dict)) or result is None:
                    return utils.reply(result, response_cls)

                streaming = True
                return cls._stream(result, response_cls, context, tails)
            except GRPCException as exc:
                context.abort(exc.code, exc.details)
            finally:
                # streaming teardown is owned by stream(); run it exactly once.
                if not streaming:
                    for tail in tails:
                        tail()

        return method

    @classmethod
    def _resolve(
        cls, fn: Callable, request: Any, context: grpc.ServicerContext
    ) -> tuple[Any, Callable[[], None]]:
        """
        Resolve one dependency (and its nested ``Depends`` tree).

        Args:
            fn: The dependency function.
            request: Request message, or the request iterator on streams.
            context: gRPC servicer context.

        Returns:
            A ``(value, tail)`` pair: the value to inject, and a callable
            running the dependency's post-handler remainder (a generator
            tail, a no-op for plain functions).

        Raises:
            StopIteration: If a generator dependency yields before any value.
        """
        kwargs: dict[str, Any] = {}
        fn_hints = hints(fn)
        for name, param in inspect.signature(fn).parameters.items():
            dep = param_dependency(param, fn_hints.get(name, param.annotation))
            if dep is not None:
                kwargs[name], _ = cls._resolve(dep.dependency, request, context)
            elif name == "request" or name == "request_iterator":
                kwargs[name] = request
            elif name == "context":
                kwargs[name] = context

        if not inspect.isgeneratorfunction(fn):
            return fn(**kwargs), noop

        def tail() -> None:
            """Drain the generator, running everything after its ``yield``."""
            for _ in gen:
                pass

        gen = fn(**kwargs)

        return next(gen), tail

    @classmethod
    def _validate_dependency(cls, fn: Callable, seen: frozenset[Callable] = frozenset()) -> None:
        """Recursively validate that a dependency declares only injectables.

        Args:
            fn: The dependency function to validate.
            seen: Already-visited dependencies, guarding against cycles.

        Raises:
            ValueError: If the dependency declares a non-injectable and
                non-``Depends`` parameter.
        """
        if fn in seen:
            return
        fn_hints = hints(fn)

        for name, param in inspect.signature(fn).parameters.items():
            dep = param_dependency(param, fn_hints.get(name, param.annotation))
            if dep is not None:
                cls._validate_dependency(dep.dependency, seen | {fn})
            elif name not in cls.INJECTABLE:
                raise ValueError(
                    f"dependency {fn.__name__!r} declares unregistrable parameter(s) "
                    f"[{name!r}]; injectable names: {list(cls.INJECTABLE)} or "
                    f"Depends(dependency) defaults"
                )

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

    def rpc(
        self,
        service: str,
        method: str,
    ) -> Callable[[Callable], Callable]:
        """Decorate a function as the handler for one RPC method.

        Args:
            service: Proto service name, e.g. ``"RouteGuide"``.
            method: RPC method name within the service, e.g. ``"GetFeature"``.
                Dependencies may be declared via ``Depends(...)`` parameter
                defaults; app-wide and signature dependencies run in that
                order, outermost first.

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
