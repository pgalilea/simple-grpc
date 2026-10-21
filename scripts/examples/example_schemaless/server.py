"""Hybrid example: a proto-backed endpoint and schemaless no-proto endpoints."""

from protos import CALC_PROTO

from simple_grpc import SimpleGRPC


app = SimpleGRPC(
    raw_protos={"calc": CALC_PROTO},
    port=50053,
)


@app.rpc("Calculator", "Add")
def add(request):
    """Schema-backed: the request is a typed message."""
    return {"sum": request.a + request.b}


@app.schemaless_rpc("Greeter", "SayHello")
def say_hello(request):
    """Schemaless: dicts in, dicts out (wire format is google.protobuf.Struct)."""
    return {"message": f"Hello, {request['name']}!"}


@app.schemaless_rpc("Greeter", "CountUp")
def count_up(request):
    """Schemaless server-streaming: a generator handler is inferred as a stream."""
    for n in range(1, int(request.get("up_to", 0)) + 1):
        yield {"n": n}


@app.schemaless_rpc("Dynamic", "GetProtos")
def expose_protos():
    return app.protos


if __name__ == "__main__":
    app.dev()  # hot reload: edit this file and the server restarts
