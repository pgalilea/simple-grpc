"""Handler dispatch: wrap registered handlers into gRPC servicer methods."""

from collections.abc import Callable, Generator, Iterable, Iterator
from functools import partial
import inspect
from typing import Any, get_origin

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message
from google.protobuf.struct_pb2 import Struct
import grpc

from simple_grpc.exceptions import GRPCException
from simple_grpc.logging import log_access
from simple_grpc.params import INJECTABLE, Depends, hints, noop, param_dependency
import simple_grpc.utils as utils


def wrap_handler(
    handler: Callable,
    response_cls: type[Message],
    dependencies: tuple[Depends, ...] = (),
    path: str = "",
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
        path: Full gRPC method path (``/pkg.Service/Method``) for the
            access log.

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
        if name not in INJECTABLE
        and param_dependency(param, fn_hints.get(name, param.annotation)) is None
    )
    if unknown:
        raise ValueError(
            f"handler {handler.__name__!r} declares unregistrable parameter(s) "
            f"{unknown}; injectable names: {list(INJECTABLE)} or "
            f"Depends(dependency) defaults"
        )
    handler_deps = [
        dep
        for param in params.values()
        for dep in [param_dependency(param, fn_hints.get(param.name, param.annotation))]
        if dep is not None
    ]

    for dep in (*handler_deps, *dependencies):
        validate_dependency(dep.dependency)

    return partial(
        dispatch,
        handler=handler,
        response_cls=response_cls,
        dependencies=dependencies,
        params=params,
        fn_hints=fn_hints,
        path=path,
    )


def dispatch(
    request: Any,
    context: grpc.ServicerContext,
    *,
    handler: Callable,
    response_cls: type[Message],
    dependencies: tuple[Depends, ...],
    params: dict[str, inspect.Parameter],
    fn_hints: dict[str, Any],
    path: str,
) -> Any:
    """
    Dispatch to the user handler with its dependencies resolved.

    Args:
        request: Request message, or the request iterator on streams.
        context: gRPC servicer context.
        handler: The user-registered handler.
        response_cls: Generated message class for the RPC's response type.
        dependencies: App-wide ``Depends``, resolved before the signature
            dependencies.
        params: The handler's inspected parameters.
        fn_hints: The handler's resolved type hints.
        path: Full gRPC method path for the access log.

    Returns:
        Whatever the handler returns, serialized for the wire.

    Raises:
        Exception: Any exception from the dependencies or handler.
            ``GRPCException`` subclasses abort the RPC with their
            status code and details; ``context.abort`` keeps the code
            it was called with; other exceptions propagate for gRPC
            to map to ``UNKNOWN``. The final status is logged through
            the access logger either way.
    """
    kwargs: dict[str, Any] = {}
    tails: list[Callable[[], None]] = []
    streaming = False
    try:
        # resolved inside the try so dependency failures get the same
        # status mapping and access logging as handler failures.
        kwargs = resolve_dependencies(
            dependencies, params, fn_hints, request, context, tails
        )
        result = handler(**kwargs)
        if isinstance(result, (Message, dict)) or result is None:
            log_access(context, path, grpc.StatusCode.OK)
            return utils.reply(result, response_cls)

        streaming = True
        return stream(result, response_cls, context, tails, path=path)
    except GRPCException as exc:
        log_access(context, path, exc.code)
        context.abort(exc.code, exc.details)
    except Exception:
        # context.code() is already set when the failure was a
        # context.abort(code, details); None otherwise.
        log_access(context, path, context.code() or grpc.StatusCode.UNKNOWN)
        raise
    finally:
        # streaming teardown is owned by stream(); run it exactly once.
        if not streaming:
            for tail in tails:
                tail()


def resolve_dependencies(
    dependencies: tuple[Depends, ...],
    params: dict[str, inspect.Parameter],
    fn_hints: dict[str, Any],
    request: Any,
    context: grpc.ServicerContext,
    tails: list[Callable[[], None]],
) -> dict[str, Any]:
    """
    Resolve the app-wide and signature dependencies for one call.

    Args:
        dependencies: App-wide ``Depends``, resolved first (outermost).
        params: The handler's inspected parameters.
        fn_hints: The handler's resolved type hints.
        request: Request message, or the request iterator on streams.
        context: gRPC servicer context.
        tails: Post-call teardowns, appended in resolution order so
            teardowns already queued before a failed dependency still run.

    Returns:
        The keyword arguments for the handler (request/context and
        the resolved dependencies).
    """
    kwargs: dict[str, Any] = {}
    for dep in dependencies:
        _, tail = resolve_dependency(dep.dependency, request, context)
        tails.append(tail)

    for name, param in params.items():
        dep = param_dependency(param, fn_hints.get(name, param.annotation))
        if dep is not None:
            kwargs[name], tail = resolve_dependency(dep.dependency, request, context)
            tails.append(tail)
        elif name == "request" or name == "request_iterator":
            kwargs[name] = request
        elif name == "context":
            kwargs[name] = context

    return kwargs


def resolve_dependency(
    fn: Callable, request: Any, context: grpc.ServicerContext
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
            kwargs[name], _ = resolve_dependency(dep.dependency, request, context)
        elif name == "request" or name == "request_iterator":
            kwargs[name] = request
        elif name == "context":
            kwargs[name] = context

    if not inspect.isgeneratorfunction(fn):
        return fn(**kwargs), noop

    gen = fn(**kwargs)
    return next(gen), partial(drain, gen)


def drain(gen: Iterator[Any]) -> None:
    """Run a generator dependency's remainder (everything after its ``yield``."""
    for _ in gen:
        pass


def validate_dependency(fn: Callable, seen: frozenset[Callable] = frozenset()) -> None:
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
            validate_dependency(dep.dependency, seen | {fn})
        elif name not in INJECTABLE:
            raise ValueError(
                f"dependency {fn.__name__!r} declares unregistrable parameter(s) "
                f"[{name!r}]; injectable names: {list(INJECTABLE)} or "
                f"Depends(dependency) defaults"
            )


def _to_dict(request: Any) -> Any:
    """
    Convert an inbound request from ``Struct`` wire format to plain dicts.

    Args:
        request: A ``Struct`` (unary) or an iterator of ``Struct``
            (request-streaming).

    Returns:
        A plain dict, or an iterator of plain dicts.
    """
    if isinstance(request, Struct):
        return MessageToDict(request)
    return (MessageToDict(message) for message in request)


def _is_response_stream(handler: Callable) -> bool:
    """
    Decide whether a schemaless handler streams its responses.

    A handler streams when it is a generator function or its return
    annotation is an ``Iterator``/``Generator``/``Iterable`` — mirroring how
    :func:`dispatch` routes non-message results at call time.

    Args:
        handler: The user-registered handler.

    Returns:
        True when the RPC should be registered as response-streaming.
    """
    if inspect.isgeneratorfunction(handler):
        return True
    origin = get_origin(hints(handler).get("return"))
    return origin in (Iterator, Generator, Iterable)


def schemaless_method_handler(
    handler: Callable,
    dependencies: tuple[Depends, ...] = (),
    path: str = "",
) -> grpc.RpcMethodHandler:
    """
    Wrap a schemaless handler into a ``grpc.RpcMethodHandler``.

    The wire format is ``google.protobuf.Struct`` on both sides: requests
    arrive as ``Struct`` and are handed to the handler as plain dicts;
    returned dicts are serialized back as ``Struct`` (a returned ``Struct``
    message passes through untouched). The RPC flavor is inferred from the
    handler shape: declaring ``request_iterator`` makes it request-streaming;
    being a generator function (or annotating an ``Iterator``/``Generator``/
    ``Iterable`` return) makes it response-streaming. Any other flavor is
    unary — annotate streaming returns, or the RPC breaks at call time.

    Args:
        handler: The user-registered handler.
        dependencies: App-wide ``Depends`` for this handler,
            resolved before (outside) the signature dependencies.
        path: Full gRPC method path (``/Service/Method``) for the
            access log.

    Returns:
        The method handler to register for this RPC.
    """
    inner = wrap_handler(handler, Struct, dependencies=dependencies, path=path)

    def behavior(request: Any, context: grpc.ServicerContext) -> Any:
        """Adapt ``Struct`` wire format to dicts, then dispatch."""
        return inner(_to_dict(request), context)

    factory = {
        (False, False): grpc.unary_unary_rpc_method_handler,
        (False, True): grpc.unary_stream_rpc_method_handler,
        (True, False): grpc.stream_unary_rpc_method_handler,
        (True, True): grpc.stream_stream_rpc_method_handler,
    }[("request_iterator" in inspect.signature(handler).parameters, _is_response_stream(handler))]
    return factory(
        behavior,
        request_deserializer=Struct.FromString,
        response_serializer=Struct.SerializeToString,
    )


def stream(
    result,
    response_cls: type[Message],
    context: grpc.ServicerContext,
    tails,
    path: str = "",
) -> Iterator[Any]:
    """
    Serialize each item, mapping GRPCException to a status.

    Teardown runs after the last item, so generator
    dependencies (e.g. DB sessions) stay alive while gRPC
    consumes the stream.

    Logs one access line when the stream finishes.

    Args:
        result: The handler's return value, iterated for stream items.
        response_cls: Generated message class for the RPC's response type.
        context: gRPC servicer context.
        tails: Post-call teardowns, run after the last item.
        path: Full gRPC method path for the access log.
    """
    try:
        for item in result:
            yield utils.reply(item, response_cls)
        log_access(context, path, grpc.StatusCode.OK)
    except GRPCException as exc:
        log_access(context, path, exc.code)
        context.abort(exc.code, exc.details)
    except Exception:
        # context.code() is already set when the failure was a
        # context.abort(code, details); None otherwise.
        log_access(context, path, context.code() or grpc.StatusCode.UNKNOWN)
        raise
    finally:
        for tail in tails:
            tail()
