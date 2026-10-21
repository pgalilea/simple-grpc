from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Unpack

from google.protobuf import message_factory
from google.protobuf.message import Message
import grpc
from grpc_tools import protoc

import simple_grpc.dispatch as dispatch
from simple_grpc.logging import (
    LoggingConfig,
    configure_logging,
    logger,
    style,
)
from simple_grpc.models import ProtoConfig, ServerCredentials, ServerOptions
from simple_grpc.params import INJECTABLE, Depends
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
    INJECTABLE = INJECTABLE  # just for compatibility

    def __init__(
        self,
        *,
        thread_pool: ThreadPoolExecutor | None = None,
        raw_protos: dict[str, str] | None = None,
        proto_path: str | Path | None = None,
        proto_files: list[str] | None = None,
        service_path: str | Path | None = None,
        port: int = 50051,
        dependencies: list[Depends] | None = None,
        credentials: ServerCredentials | None = None,
        logging_config: LoggingConfig | None = None,
        **server_options: Unpack[ServerOptions],
    ) -> None:
        """
        Store the compile config; handlers attach later via :meth:`rpc`.

        Raises:
            ValueError: If ``raw_protos`` is passed together with
                ``proto_path`` or ``proto_files`` - provide protos either as
                inline content or as files on disk, not both.

        Args:
            thread_pool: Executor used to serve requests. Defaults to a new
                ``ThreadPoolExecutor``.
            raw_protos: Proto content as inline strings, keyed by the stem to
                give the generated modules (``{"calc": "syntax..."}`` ->
                ``calc_pb2``). Materialized into a content-addressed dir under
                ``tempfile.gettempdir()``; identical content maps to the
                identical dir, so a client constructing the app with the same
                content finds the generated code without coordination.
            proto_path: Directory containing ``.proto`` files. ``None``
                (default) resolves to the raw-proto dir in raw mode, else
                ``"protos"``.
            proto_files: Proto files to compile; ``None`` (default) discovers
                all ``*.proto`` in ``proto_path`` in file mode, or uses the
                ``raw_protos`` keys in raw mode.
            service_path: Output directory for the generated ``*_pb2*.py``.
                ``None`` (default) resolves to the raw-proto dir in raw mode,
                else ``"grpc_services"``. An explicitly passed value wins in
                both modes (the client passes the same one when rendezvousing).
            port: Port the server binds with ``dev()``/``run()``.
        credentials: TLS credentials; each PEM field takes a ``Path`` or
                the content itself. Given, ``_start`` binds with
                ``add_secure_port`` (and ``ssl_server_credentials``); absent,
                the port stays insecure.
            logging_config: Logging knobs (access log, level, colors);
                defaults to ``LoggingConfig()``.
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
        self.proto_config = ProtoConfig.config_proto_files(
            raw_protos=raw_protos,
            proto_path=proto_path,
            proto_files=proto_files,
            service_path=service_path,
        )
        self.port = port
        self.credentials = credentials
        self.logging_config = logging_config or LoggingConfig()
        self._handlers: dict[str, dict[str, Callable]] = {}
        self._schemaless_handlers: dict[str, dict[str, Callable]] = {}
        self._app_dependencies: tuple[Depends, ...] = tuple(dependencies or ())
        self._server_options: ServerOptions = server_options

    def _start(self) -> None:
        """Compile, load generated modules, register services, and serve."""

        configure_logging(self.logging_config)
        logger.info("Starting simple_grpc (pid %s)", os.getpid())
        mods: dict[str, tuple[ModuleType, ModuleType]] = {}
        if self._handlers:
            self._compile()
            proto = self.proto_config
            logger.info(
                "Compiled %d proto file(s) from '%s' into '%s'",
                len(proto.proto_files),
                proto.proto_path,
                proto.service_path,
            )
            mods = self._load_generated()
        server = grpc.server(self.thread_pool, **self._server_options)
        self._register_services(mods, server)
        self._register_schemaless(server)
        registered = {**self._handlers, **self._schemaless_handlers}
        services = ", ".join(f"{svc} ({', '.join(m)})" for svc, m in registered.items())
        logger.info("Registered services: %s", services or "(none)")

        if self.credentials:
            server.add_secure_port(f"[::]:{self.port}", self.credentials.to_server_credentials())
            tls = "(TLS)"
        else:
            server.add_insecure_port(f"[::]:{self.port}")
            tls = f"({style('No', self.logging_config.use_colors, bold=True)} TLS)"

        server.start()
        logger.info("Serving on [::]:%d %s - press CTRL+C to quit", self.port, tls)
        server.wait_for_termination()

    def _compile(self) -> None:
        """
        Run protoc against the configured proto files.

        Raises:
            RuntimeError: If ``protoc`` exits with a non-zero status.
        """
        if not self.proto_config.service_path.exists():  # type: ignore[reportOptionalMemberAccess]
            raise ValueError(f"service_path '{self.proto_config.service_path}' does not exist.")
        if len(self.proto_config.proto_files) == 0:
            raise RuntimeError(
                f"no proto files in directory: {self.proto_config.proto_path} - "
                f"proto-backed @app.rpc handlers require a .proto file; or use "
                f"@app.schemaless_rpc for endpoints without a schema"
            )
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
        package = pb2.DESCRIPTOR.package
        prefix = f"{package}.{service}" if package else service
        overrides = {
            name: self._wrap_handler(
                handler,
                message_factory.GetMessageClass(methods[name].output_type),
                self._app_dependencies,
                path=f"/{prefix}/{name}",
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

    def _register_schemaless(self, server: grpc.Server) -> None:
        """
        Attach one generic RPC handler per schemaless service.

        Args:
            server: The server receiving the generic RPC handlers.
        """
        for service, methods in self._schemaless_handlers.items():
            method_handlers = {
                name: dispatch.schemaless_method_handler(
                    handler,
                    dependencies=self._app_dependencies,
                    path=f"/{service}/{name}",
                )
                for name, handler in methods.items()
            }
            server.add_generic_rpc_handlers(
                (grpc.method_handlers_generic_handler(service, method_handlers),)
            )

    @classmethod
    def _stream(
        cls,
        result,
        response_cls: type[Message],
        context: grpc.ServicerContext,
        tails,
        path: str = "",
    ) -> Iterator[Any]:
        """Delegate to :func:`simple_grpc.dispatch.stream`."""
        return dispatch.stream(result, response_cls, context, tails, path=path)

    @classmethod
    def _wrap_handler(
        cls,
        handler: Callable,
        response_cls: type[Message],
        dependencies: tuple[Depends, ...] = (),
        path: str = "",
    ) -> Callable:
        """Delegate to :func:`simple_grpc.dispatch.wrap_handler`."""
        return dispatch.wrap_handler(handler, response_cls, dependencies=dependencies, path=path)

    @property
    def protos(self) -> dict[str, str]:
        """
        Raw content of every configured ``.proto`` file, keyed by file name.

        Read on access, so edits to the proto files show up under hot reload.
        """
        return {
            pf: (self.proto_config.proto_path / pf).read_text(encoding="utf-8")
            for pf in self.proto_config.proto_files
        }

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

    def schemaless_rpc(
        self,
        service: str,
        method: str,
    ) -> Callable[[Callable], Callable]:
        """
        Decorate a function as the handler for one RPC, with no proto schema.

        The endpoint is served without any ``.proto`` definition, using
        ``google.protobuf.Struct`` as the wire format on both sides: the
        handler receives the request as a plain dict and returns a plain
        dict (or a ``Struct`` message, or ``None``), exactly like the dict
        contract of :meth:`rpc`.

        The RPC flavor is inferred from the handler shape: declaring
        ``request_iterator`` makes it request-streaming; being a generator
        function (or annotating an ``Iterator``/``Generator``/``Iterable``
        return) makes it response-streaming; anything else is unary.
        Dependencies may be declared via ``Depends(...)`` parameter
        defaults, like :meth:`rpc`.

        Args:
            service: Service name as clients address it, e.g.
                ``"Greeter"`` or a dotted ``"pkg.Greeter"``. The full method
                path is ``/{service}/{method}``.
            method: RPC method name within the service.

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
            self._schemaless_handlers.setdefault(service, {})[method] = func
            return func

        return decorator

    def dev(
        self,
        reload: bool = True,
        reload_dirs: list[str | Path] | None = None,
        reload_excludes: list[str | Path] | None = None,
    ) -> None:
        """
        Serve with hot reload: editing watched files restarts the server.

        The app runs in a spawned child process, rebuilt from its module on
        every restart (fresh imports, fresh ``protoc`` compile). By default
        the working directory is watched, excluding the proto and service
        directories plus the generated ``*_pb2*.py`` (protoc rewrites them
        at every child start, which would otherwise loop reloads forever).

        Args:
            reload: Serve in-process like :meth:`run` when False.
            reload_dirs: Directories to watch; defaults to the working
                directory.
            reload_excludes: Extra glob patterns and/or directories to
                ignore, on top of the defaults (proto/service directories,
                ``*_pb2.py``/``*_pb2_grpc.py``).

        Raises:
            ImportError: If ``watchfiles`` is not installed.
            RuntimeError: If the app is not bound to a module-level
                variable, or if ``protoc`` exits with a non-zero status.
        """
        configure_logging(self.logging_config)

        if not reload:
            self._start()
            return

        try:
            from simple_grpc import reloading
        except ImportError as exc:
            raise ImportError(
                "hot reload requires watchfiles: install it with 'pip install watchfiles'"
            ) from exc

        module_ref, attr = reloading.locate_app(self, sys._getframe(1))
        reloading.serve_with_reload(
            self,
            module_ref,
            attr,
            reload_dirs=reload_dirs,
            reload_excludes=reload_excludes,
        )

    def run(self) -> None:
        """Serve like :meth:`dev`; compile-skipping is deferred until needed.

        Raises:
            RuntimeError: If ``protoc`` exits with a non-zero status.
        """
        self._start()
