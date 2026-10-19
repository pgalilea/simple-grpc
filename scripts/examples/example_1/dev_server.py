"""Basic SimpleGRPC example in dev mode — the proto is written to the tmp dir."""

from pathlib import Path
import tempfile

from simple_grpc import SimpleGRPC


TMP_DIR = Path(tempfile.gettempdir()) / "simple_grpc_example_1"
TMP_DIR.mkdir(exist_ok=True)
(TMP_DIR / "greeter.proto").write_text("""
syntax = "proto3";

service Greeter {
  rpc SayHello (HelloRequest) returns (HelloReply);
}

message HelloRequest {
  string name = 1;
}

message HelloReply {
  string message = 1;
}

service Calculator {
  rpc Add (AddRequest) returns (AddReply);
  rpc CountUp (CountUpRequest) returns (stream CountUpReply);
}

message AddRequest {
  int64 a = 1;
  int64 b = 2;
}

message AddReply {
  int64 sum = 1;
}

message CountUpRequest {
  int64 up_to = 1;
}

message CountUpReply {
  int64 n = 1;
}
""")

app = SimpleGRPC(
    proto_path=TMP_DIR,
    proto_files=["greeter.proto"],
    service_path=TMP_DIR,  # generated *_pb2*.py land next to the proto, in tmp
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
