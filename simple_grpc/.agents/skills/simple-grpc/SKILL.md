---
name: simple-grpc
description: SimpleGRPC best practices and conventions. Use when working with SimpleGRPC apps, RPC handlers (proto-backed and schemaless), dependency injection, streaming, typed exceptions, and serving gRPC services. Keeps SimpleGRPC code clean and up to date with the latest features and patterns.
---

# SimpleGRPC

Skill for writing SimpleGRPC services with best practices.

## Quick Reference

* Serve the app: `app.dev()` for hot reload, `app.run()` for production; see [Serve the App](#serve-the-app).
* Proto-backed RPCs: `@app.rpc("Service", "Method")`, handler receives a typed message; see [Proto-Backed RPCs](#proto-backed-rpcs-apprpc).
* Schemaless RPCs: `@app.schemaless_rpc("Service", "Method")`, dicts in, dicts out, no proto files; see [Schemaless RPCs](#schemaless-rpcs-appschemaless_rpc).
* Dependencies: use `Annotated[..., Depends(...)]`; see [Dependency Injection](#dependency-injection).
* Streaming: the RPC flavor is inferred from the handler shape; always annotate streaming returns; see [Streaming](#streaming).
* Errors: raise the typed exceptions (`NotFoundError`, `InvalidArgumentError`, ...); see [Error Handling](#error-handling).
* Injectable parameters: only `request`, `request_iterator`, and `context`, by name; everything else must be a `Depends`.

## Serve the App

Serve with hot reload (requires `watchfiles`; edits restart the server, recompiling protos):

```python
if __name__ == "__main__":
    app.dev()
```

Serve for production:

```python
if __name__ == "__main__":
    app.run()
```

## Use `Annotated`

Always prefer the `Annotated` style for dependency declarations. It keeps the types visible and allows reusing the dependency across handlers via a type alias:

```python
from typing import Annotated

from simple_grpc import Depends, SimpleGRPC

app = SimpleGRPC(port=50051)


def get_current_user(context) -> str:
    metadata = dict(context.invocation_metadata())
    return metadata.get("authorization", "")


UserDep = Annotated[str, Depends(get_current_user)]


@app.schemaless_rpc("Notes", "WhoAmI")
def who_am_i(request, user: UserDep) -> dict:
    return {"user": user}
```

The plain default form also works, use it for side-effect dependencies that produce no value:

```python
def log_dep(request, context) -> None:
    print(f"log: -> {type(request).__name__}")
    yield
    print(f"log: <- {type(request).__name__}")


@app.schemaless_rpc("Notes", "GetNote")
def get_note(request, _log=Depends(log_dep)) -> dict:
    return {"title": "shopping"}
```

## Proto-Backed RPCs (`@app.rpc`)

Declare the schema once, pass it inline via `raw_protos` (keyed by file stem, compiled at startup; identical content maps to the same generated directory, so a client constructing the app with the same protos finds the code without coordination):

```python
CALCULATOR_PROTO = """
syntax = "proto3";

service Calculator {
  rpc Add (AddRequest) returns (AddReply);
}

message AddRequest {
  int64 a = 1;
  int64 b = 2;
}

message AddReply {
  int64 sum = 1;
}
"""

app = SimpleGRPC(raw_protos={"calculator": CALCULATOR_PROTO}, port=50052)


@app.rpc("Calculator", "Add")
def add(request) -> dict:
    return {"sum": request.a + request.b}
```

Use `proto_path` and `proto_files` instead of `raw_protos` when the `.proto` files live on disk.

Return a plain dict and the framework serializes it into the reply message; returning the message itself also works. One function per RPC method, matching the service and method names of the proto exactly (a mismatch raises `ValueError` at startup listing the available methods).

## Schemaless RPCs (`@app.schemaless_rpc`)

When there is no schema, use `@app.schemaless_rpc`. The wire format is `google.protobuf.Struct` on both sides: the handler receives a plain dict and returns a plain dict. No proto file, no build step, no generated code:

```python
app = SimpleGRPC(port=50051)


@app.schemaless_rpc("Greeter", "SayHello")
def say_hello(request) -> dict:
    return {"message": f"Hello, {request['name']}!"}
```

Schemaless handlers support the same `Depends` and injectable parameters as `@app.rpc`. Use them for dynamic payloads, hybrid services, or rapid prototyping; use `@app.rpc` when the contract should be typed and shared with other clients.

## Streaming

The RPC flavor is inferred from the handler shape, not declared:

* Declaring `request_iterator` makes it request-streaming.
* Being a generator function, or annotating an `Iterator`/`Generator`/`Iterable` return, makes it response-streaming.
* Anything else is unary.

Always annotate streaming returns, or the RPC registers as unary and breaks at call time:

```python
from collections.abc import Iterator

from simple_grpc import SimpleGRPC

app = SimpleGRPC(raw_protos={"greeter": GREETER_PROTO}, port=50052)


@app.rpc("Calculator", "CountUp")
def count_up(request) -> Iterator[dict]:
    for n in range(1, request.up_to + 1):
        yield {"n": n}
```

Stream items may be dicts or messages. Dependencies with `yield` stay open while gRPC consumes the stream; their teardown runs after the last item, so DB sessions are safe to use per item.

## Dependency Injection

Dependencies are plain functions. They may declare the injectable parameters (`request`, `request_iterator`, `context`) or further `Depends(...)` declarations, forming a tree resolved depth-first:

```python
from typing import Annotated

from simple_grpc import Depends, SimpleGRPC, status

app = SimpleGRPC(port=50054)

VALID_TOKENS = {"abc123": "alice", "def456": "bob"}


def auth_dep(context) -> str:
    metadata = dict(context.invocation_metadata())
    auth = metadata.get("authorization", "")
    scheme, _, token = auth.partition(" ")

    if scheme.lower() != "bearer" or token not in VALID_TOKENS:
        context.abort(status.UNAUTHENTICATED, "invalid bearer token")

    return token


def current_user(token: Annotated[str, Depends(auth_dep)]) -> str:
    return VALID_TOKENS[token]


UserDep = Annotated[str, Depends(current_user)]


@app.schemaless_rpc("Notes", "WhoAmI")
def who_am_i(request, user: UserDep) -> dict:
    return {"user": user}
```

A `yield`-based dependency runs its body before the handler and the remainder after it responds (open before, close after):

```python
def db_session() -> dict:
    print("db: session opened")
    yield {"notes": ["shopping", "taxes"]}
    print("db: session closed")


@app.schemaless_rpc("Notes", "GetNote")
def get_note(request, db: Annotated[dict, Depends(db_session)]) -> dict:
    return {"title": db["notes"][int(request["id"]) - 1]}
```

Use dependencies when the logic depends on external resources (auth headers, DB sessions), needs cleanup with `yield`, or is shared across handlers. Apply shared dependencies app-wide via the constructor; they run before every RPC, outermost first:

```python
app = SimpleGRPC(port=50051, dependencies=[Depends(auth_dep)])
```

## Injectable Parameters

Handlers may declare any subset of `request` (the request message, or the request iterator on streaming RPCs), `request_iterator` (alias, same object), and `context`, by name. Any other parameter must be a `Depends(...)` declaration, or startup validation fails with a `ValueError` listing the offending names.

Prefer declaring only what the handler uses; undeclared parameters are simply not injected.

## Error Handling

Raise the typed exceptions for a clean abort with the right status code, on unary and streaming calls alike:

```python
from simple_grpc import SimpleGRPC
from simple_grpc.exceptions import NotFoundError

app = SimpleGRPC(raw_protos={"notes": NOTES_PROTO}, port=50054)


@app.rpc("Notes", "GetNote")
def get_note(request) -> dict:
    if request.id not in (1, 2):
        raise NotFoundError(f"note {request.id} not found")
    return {"id": request.id}
```

There is one exception class per gRPC status code (`NotFoundError`, `InvalidArgumentError`, `UnauthenticatedError`, `PermissionDeniedError`, ...). Calling `context.abort(status.X, "details")` directly is equivalent. Any other exception propagates and gRPC maps it to `UNKNOWN`; the access log records the final status either way.

## Tooling

Run the tests with uv and pytest:

```bash
uv run pytest -vs
```
