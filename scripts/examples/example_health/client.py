"""Client: checks the standard grpc.health.v1.Health service, then calls Greeter."""

import sys

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
from protos import GREETER_PROTO

from simple_grpc import SimpleGRPC


# same content as server.py -> same content-addressed dir -> same generated code
sys.path.insert(0, str(SimpleGRPC(raw_protos={"greeter": GREETER_PROTO}).proto_config.service_path))

import greeter_pb2 as pb2  # type: ignore # noqa: E402
from greeter_pb2_grpc import GreeterStub  # type: ignore # noqa: E402


with grpc.insecure_channel("localhost:50055") as channel:
    # health check - what k8s/Envoy/grpcurl probe out of the box
    health = health_pb2_grpc.HealthStub(channel)
    status = health.Check(health_pb2.HealthCheckRequest(service="")).status
    assert status == health_pb2.HealthCheckResponse.SERVING, status

    # the actual service
    reply = GreeterStub(channel).SayHello(pb2.HelloRequest(name="dev mode"))
    assert reply.message == "Hello, dev mode!", reply.message

print("health + RPCs OK")
