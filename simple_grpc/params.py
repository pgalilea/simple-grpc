from collections.abc import Callable
from dataclasses import dataclass
import inspect
from typing import Annotated, Any, get_args, get_origin, get_type_hints


@dataclass(frozen=True)
class Depends:
    """Dependency marker for RPC handler parameters.

    Wrap a plain function in ``Depends`` as a parameter default (or inside an
    ``Annotated``) so the framework resolves the value by calling it before
    the handler runs::

        def get_token(context):
            ...

        CommonsDep = Annotated[str, Depends(get_token)]

        @app.rpc("Echo", "Say")
        def say(request, token: CommonsDep):
            ...

    The dependency itself is a plain function and may declare any injectable
    parameter (``request``, ``request_iterator``, ``context``) or further
    ``Depends(...)`` declarations, forming a dependency tree resolved
    depth-first. A ``yield``-based dependency runs its
    body before the handler and the remainder after it responds.

    Attributes:
        dependency: The function called to produce the injected value.
    """

    dependency: Callable[..., Any]


# Parameter names a handler or dependency may declare for injection.
INJECTABLE = ("request", "request_iterator", "context")


def noop() -> None:
    """No-op teardown for plain-function dependencies."""
    return None


def hints(fn: Callable) -> dict[str, Any]:
    """Resolve a function's annotations, preserving ``Annotated`` extras.

    Args:
        fn: The handler or dependency to inspect.

    Returns:
        Mapping of parameter name to annotation with extras, or ``{}`` when
        the annotations cannot be evaluated (their objects still win via
        ``param.annotation`` fallbacks).
    """

    try:
        return get_type_hints(fn, include_extras=True)
    except (TypeError, NameError):
        return {}


def param_dependency(param: inspect.Parameter, hint: Any) -> Depends | None:
    """Find the ``Depends`` declared for one parameter, if any.

    A declaration is either a ``Depends(...)`` default value or a ``Depends``
    instance inside the metadata of an ``Annotated`` type::

        token=Depends(get_token)          # default form
        token: CommonsDep                 # Annotated form, no default

    Args:
        param: The inspected parameter.
        hint: The resolved annotation for the parameter (from :func:`hints`),
            or the raw ``param.annotation`` when introspection failed.

    Returns:
        The declared :class:`Depends`, or ``None`` when the parameter is not
        a dependency.
    """

    if isinstance(param.default, Depends):
        return param.default
    if get_origin(hint) is Annotated:
        for meta in get_args(hint)[1:]:
            if isinstance(meta, Depends):
                return meta
    return None
