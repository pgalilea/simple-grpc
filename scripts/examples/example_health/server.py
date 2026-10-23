"""Health checking example - ``SimpleGRPC(health=True)`` serves grpc.health.v1.Health.

Run from this folder: ``python server.py`` (dev mode), then ``python client.py``.
"""

from protos import GREETER_PROTO

from simple_grpc import SimpleGRPC


app = SimpleGRPC(
    raw_protos={"greeter": GREETER_PROTO},
    port=50055,
    health=True,  # exposes grpc.health.v1.Health (Check + Watch), SERVING by default
)


@app.rpc("Greeter", "SayHello")
def say_hello(request):
    """Return a greeting for the requested name."""
    return {"message": f"Hello, {request.name}!"}


if __name__ == "__main__":
    app.dev()  # hot reload: edit this file and the server restarts
