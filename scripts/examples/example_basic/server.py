"""Basic SimpleGRPC example in dev mode — the schema is inline proto content."""

from protos import GREETER_PROTO

from simple_grpc import SimpleGRPC


app = SimpleGRPC(
    raw_protos={"greeter": GREETER_PROTO},
    port=50052,
)


@app.rpc("Greeter", "SayHello")
def say_hello(request):
    """Return a greeting for the requested name."""
    return {"message": f"Hello, {request.name}!"}


@app.rpc("Calculator", "Add")
def add(request):
    """Sum two integers."""
    return {"sum": request.a + request.b}


@app.rpc("Calculator", "CountUp")
def count_up(request):
    """Yield 1..up_to, one number at a time."""
    for n in range(1, request.up_to + 1):
        yield {"n": n}


if __name__ == "__main__":
    app.dev()  # hot reload: edit this file and the server restarts
