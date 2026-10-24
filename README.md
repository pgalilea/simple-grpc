<p align="center">
  <a href="https://github.com/pgalilea/simple-grpc"><img src="https://i.ibb.co/Qv3LjyDz/simplegrpc-logo.png" alt="SimpleGRPC"></a>
</p>

<br/>

<p align="center"><em>High-performance gRPC made simple. Write less, ship to production faster.</em></p>

Development using [gRPC](https://grpc.io/docs/what-is-grpc/introduction/) made easy. Heavily inspired by [FastAPI](https://fastapi.tiangolo.com/).

**Important Disclaimer**: In active development. Lock the package version and run your own tests.

## 🔥 Key Features

- **Familiar, minimal API**: an app object with a single decorator - @app.rpc("Service", "Method") - maps plain Python functions onto gRPC handlers, FastAPI-style.
- **Zero protobuf boilerplate**: .proto files are auto-discovered, compiled with protoc, and wired to your handlers at startup - no build step, no generated code to check in.
- **FastAPI-style dependency injection**: Depends() with nested dependency trees, caching-free depth-first resolution, and generator dependencies with pre/post teardown (yield), plus app-wide dependencies.
- **Built-in injectables**: handlers receive request, request_iterator, and context by parameter name - no protocol noise.
- **Schemaless RPCs**: @app.schemaless_rpc("Service", "Method") - build protobuf-free services over google.protobuf.Struct, sending and receiving plain dicts.
- **Full streaming support**: all four RPC flavors out of the box - unary, request-streaming, response-streaming, and bidirectional - response streams can yield messages or dicts.
- **Typed exceptions for every gRPC status code**: 16 concrete exceptions (NotFoundError, InvalidArgumentError, UnauthenticatedError, …) that auto-map to abort() on unary and streaming calls alike.
- **First-class TLS/mTLS**: ServerCredentials accepts paths or raw PEM, validates certificates fail-fast at startup, and supports client-auth.
- **Hot-reload dev mode**: app.dev() watches your .py and .proto files, recompiles and restarts on change - the gRPC developer loop FastAPI users expect.
- **Production-grade logging out of the box**: colorized access logs per RPC plus lifecycle/error loggers, configurable via LoggingConfig and honoring NO_COLOR.
- **Full access to gRPC internals when you need them**: pass interceptors, compression, channel options, xds, and concurrency limits straight through ServerOptions to grpc.server().
- **Small and inspectable**: Minimal abstraction, minimal dependencies, no magic you can't read.



## ⚙️ Installation

From pypi:

```bash
pip install simple-grpcio
```

The package name on PyPI ends in "**io**" because the desired name was already taken 🥲.

## 🚀 Quickstart

Server-side snippets: each one is a complete, runnable file (`python server.py`).

### Schemaless endpoints

No proto files at all - plain dicts in, dicts out.

```python
from simple_grpc import SimpleGRPC

app = SimpleGRPC(port=50051)


@app.schemaless_rpc("Greeter", "SayHello")
def say_hello(request):
    return {"message": f"Hello, {request['name']}!"}


if __name__ == "__main__":
    app.run()
```



### Proto-backed endpoints

Pass proto content inline; it is compiled at startup into a generated-code directory under the system temp dir - no `.proto` file, no build step.

```python
from simple_grpc import SimpleGRPC

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
def add(request):
    return {"sum": request.a + request.b}


if __name__ == "__main__":
    app.run()
```



### Dependency injection

Declare `Depends()` in the handler signature. Plain dependencies resolve before the call; generator dependencies (`yield`) open and tear down around it.

```python
from typing import Annotated

from simple_grpc import Depends, SimpleGRPC

app = SimpleGRPC(port=50053)


def auth_dep(context):
    """Read the bearer token from the metadata headers."""
    metadata = dict(context.invocation_metadata())
    if metadata.get("authorization") != "Bearer abc123":
        context.abort(16, "unauthenticated")
    return "alice"


UserDep = Annotated[str, Depends(auth_dep)]


def db_session():
    """Open a fake session for the handler and close it afterwards."""
    print("db: session opened")
    yield {"notes": ["shopping", "taxes"]}
    print("db: session closed")


@app.schemaless_rpc("Notes", "GetNote")
def get_note(request, user: UserDep, db: Annotated[dict, Depends(db_session)]):
    return {"user": user, "title": db["notes"][int(request["id"]) - 1]}


if __name__ == "__main__":
    app.run()
```

During development, swap `app.run()` for `app.dev()` to hot-reload on file changes.

[More examples...](scripts/examples/)

### Health checking

Pass `health=True` to expose the standard `grpc.health.v1.Health` service (the gRPC health checking protocol): Kubernetes, Envoy and `grpcurl` can probe the server with zero extra code.

```python
from grpc_health.v1 import health_pb2

from simple_grpc import SimpleGRPC

app = SimpleGRPC(port=50053, health=True)  # reports SERVING for the whole server

if __name__ == "__main__":
    app.run()
```

To report a degraded state, flip the status on the servicer (available after the server starts):

```python
app.health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
```

### Reflection

Pass `reflection=True` to expose the standard gRPC server reflection service: clients and tools (`grpcurl`, Postman, Insomnia) discover the served services and their schema at runtime, without the `.proto` files compiled in.

```python
from simple_grpc import SimpleGRPC

app = SimpleGRPC(port=50058, reflection=True)

if __name__ == "__main__":
    app.run()
```

```sh
grpcurl -plaintext localhost:50058 list
grpcurl -plaintext -d '{"name": "grpcurl"}' localhost:50058 Greeter/SayHello
```

Reflection is aimed at testing and tooling - consider disabling (or restricting) it in production.

## 🧪 Tests

```sh
uv run pytest -vs
```

