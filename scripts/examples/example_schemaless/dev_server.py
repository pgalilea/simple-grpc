"""Hybrid example: a proto-backed endpoint and schemaless no-proto endpoints."""

from pathlib import Path
import tempfile

from simple_grpc import SimpleGRPC


TMP_DIR = Path(tempfile.gettempdir()) / "simple_grpc_example_2"
TMP_DIR.mkdir(exist_ok=True)
(TMP_DIR / "calc.proto").write_text("""
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
""")

app = SimpleGRPC(
    proto_path=TMP_DIR,
    proto_files=["calc.proto"],
    service_path=TMP_DIR,  # generated *_pb2*.py land next to the proto, in tmp
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


if __name__ == "__main__":
    app.dev()  # hot reload: edit this file and the server restarts
