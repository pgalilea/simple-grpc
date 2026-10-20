"""Client for dev_server.py: typed stub for proto RPCs, Struct calls for schemaless ones."""

from pathlib import Path
import sys
import tempfile

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict
import grpc


TMP_DIR = Path(tempfile.gettempdir()) / "simple_grpc_example_2"
sys.path.insert(0, str(TMP_DIR))

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
