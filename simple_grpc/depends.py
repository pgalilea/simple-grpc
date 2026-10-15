from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


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
