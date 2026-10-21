"""Client: typed stub for proto RPCs, Struct calls for schemaless ones."""

import sys

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict
import grpc
from protos import CALC_PROTO

from simple_grpc import SimpleGRPC


# same content as server.py -> same content-addressed dir -> same generated code
sys.path.insert(0, str(SimpleGRPC(raw_protos={"calc": CALC_PROTO}).proto_config.service_path))

from calc_pb2 import AddRequest  # type: ignore # noqa: E402
from calc_pb2_grpc import CalculatorStub  # type: ignore # noqa: E402


Struct = struct_pb2.Struct

with grpc.insecure_channel("localhost:50053") as channel:
    # schema-backed: generated typed stub
    sum_result = CalculatorStub(channel).Add(AddRequest(a=2, b=3)).sum
    print(sum_result)
    assert sum_result == 5

    # schemaless: generic channel calls with Struct on the wire
    say_hello = channel.unary_unary(
        "/Greeter/SayHello",
        request_serializer=Struct.SerializeToString,
        response_deserializer=Struct.FromString,
    )
    reply = MessageToDict(say_hello(ParseDict({"name": "dynamic mode"}, Struct())))
    print(reply["message"])
    assert reply["message"] == "Hello, dynamic mode!", reply

    count_up = channel.unary_stream(
        "/Greeter/CountUp",
        request_serializer=Struct.SerializeToString,
        response_deserializer=Struct.FromString,
    )
    nums = [MessageToDict(r)["n"] for r in count_up(ParseDict({"up_to": 4}, Struct()))]
    print(nums)
    # Struct stores every number as a double, so 1 arrives as 1.0
    assert nums == [1.0, 2.0, 3.0, 4.0], nums

print("\nall RPCs OK")
