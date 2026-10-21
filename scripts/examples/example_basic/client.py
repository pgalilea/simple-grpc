"""Client: imports the generated modules from the raw-proto rendezvous dir."""

import sys

import grpc
from protos import GREETER_PROTO

from simple_grpc import SimpleGRPC


# same content as server.py -> same content-addressed dir -> same generated code
sys.path.insert(0, str(SimpleGRPC(raw_protos={"greeter": GREETER_PROTO}).proto_config.service_path))

import greeter_pb2 as pb2  # type: ignore # noqa: E402
from greeter_pb2_grpc import CalculatorStub, GreeterStub  # type: ignore # noqa: E402


with grpc.insecure_channel("localhost:50052") as channel:
    stub = GreeterStub(channel)
    reply = stub.SayHello(pb2.HelloRequest(name="dev mode"))
    assert reply.message == "Hello, dev mode!", reply.message

    calc = CalculatorStub(channel)
    assert calc.Add(pb2.AddRequest(a=2, b=3)).sum == 5
    assert [r.n for r in calc.CountUp(pb2.CountUpRequest(up_to=4))] == [1, 2, 3, 4]

print("all RPCs OK")
