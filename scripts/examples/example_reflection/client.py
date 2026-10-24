"""Client: discovers services via reflection - no protos compiled in.

With reflection enabled, the server hands out its own schema at runtime, so
clients and tools need no ``.proto`` files ahead of time. Once the server
runs, try the same discovery from the shell:

    grpcurl -plaintext localhost:50058 list
    grpcurl -plaintext localhost:50058 describe Greeter
    grpcurl -plaintext -d '{"name": "grpcurl"}' localhost:50058 Greeter/SayHello
"""

import grpc
from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc


with grpc.insecure_channel("localhost:50058") as channel:
    # ask the server what it serves - the "menu", served by the server itself
    stub = reflection_pb2_grpc.ServerReflectionStub(channel)
    request = reflection_pb2.ServerReflectionRequest(list_services="")
    responses = stub.ServerReflectionInfo(iter([request]))  # type: ignore[reportCallIssue]
    names = [svc.name for response in responses for svc in response.list_services_response.service]
    assert "Greeter" in names, names

print("reflection discovery OK:", names)
