"""Reflection example - ``SimpleGRPC(reflection=True)`` serves gRPC server reflection.

Run from this folder: ``python server.py`` (dev mode), then ``python client.py``.
"""

from protos import GREETER_PROTO

from simple_grpc import SimpleGRPC


app = SimpleGRPC(
    raw_protos={"greeter": GREETER_PROTO},
    port=50058,
    reflection=True,  # grpcurl/Postman/Insomnia discover services + schemas at runtime
)


@app.rpc("Greeter", "SayHello")
def say_hello(request):
    """Return a greeting for the requested name."""
    return {"message": f"Hello, {request.name}!"}


if __name__ == "__main__":
    app.dev()  # hot reload: edit this file and the server restarts
