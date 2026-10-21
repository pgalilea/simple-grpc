<p align="center">
  <a href="https://github.com/pgalilea/simple-grpc"><img src="https://i.ibb.co/Qv3LjyDz/simplegrpc-logo.png" alt="SimpleGRPC"></a>
</p>

<br/>
<p align="center"><em>High-performance gRPC made simple. Write less, ship to production faster.</em></p>
<br/><br/>

**Important Disclaimer**: In active development. Lock the package version and run your own tests.

Development using gRPC made easy. Heavily inspired by FastAPI.

##  🔥 Key Features
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
- **Small and inspectable**: ~1,500 lines of code across 8 modules, minimal dependencies, no magic you can't read.


## ⚙️ Installation

From pypi:
```bash
pip install simple-grpc
```

## 🚀 Quickstart

### Schemaless endpoints
You can expose an endpoint, even without proto files. 
```Python



```


[More examples...](scripts/examples/)

## 🧪 Tests
```sh
uv run pytest tests -vs
```

