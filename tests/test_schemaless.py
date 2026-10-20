from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict
import grpc
import pytest

from simple_grpc import SimpleGRPC
from simple_grpc.dispatch import schemaless_method_handler


Struct = struct_pb2.Struct


def test_unary_roundtrip(mocker):
    """A dict request arrives as a dict; a dict reply leaves as a Struct."""
    handler = schemaless_method_handler(
        lambda request: {"message": f"Hello, {request['name']}!"},
        path="/Greeter/SayHello",
    )

    request = ParseDict({"name": "hi"}, Struct())
    response = handler.unary_unary(request, mocker.Mock())

    assert isinstance(response, Struct)
    assert MessageToDict(response) == {"message": "Hello, hi!"}


def test_struct_message_passthrough(mocker):
    """A returned Struct message passes through untouched."""
    reply = ParseDict({"message": "raw"}, Struct())
    handler = schemaless_method_handler(lambda request: reply, path="/S/M")

    response = handler.unary_unary(ParseDict({}, Struct()), mocker.Mock())

    assert response is reply


def test_response_streaming_inferred_from_generator(mocker):
    """A generator handler registers as unary-stream and yields Structs."""

    def count_up(request):
        for n in range(1, int(request["up_to"]) + 1):
            yield {"n": n}

    handler = schemaless_method_handler(count_up, path="/Greeter/CountUp")
    assert handler.unary_stream is not None

    request = ParseDict({"up_to": 3}, Struct())
    nums = [MessageToDict(r)["n"] for r in handler.unary_stream(request, mocker.Mock())]
    assert nums == [1.0, 2.0, 3.0]  # Struct numbers are doubles


def test_response_streaming_inferred_from_annotation(mocker):
    """An Iterator-annotated return registers as unary-stream too."""

    def count_up(request) -> Iterator[dict]:
        return ({"n": n} for n in range(1, int(request["up_to"]) + 1))

    handler = schemaless_method_handler(count_up, path="/Greeter/CountUp")

    request = ParseDict({"up_to": 2}, Struct())
    nums = [MessageToDict(r)["n"] for r in handler.unary_stream(request, mocker.Mock())]
    assert nums == [1.0, 2.0]


def test_request_streaming_inferred_from_request_iterator(mocker):
    """A request_iterator handler registers as stream-unary; items arrive as dicts."""

    def total(request_iterator):
        return {"total": sum(int(item["n"]) for item in request_iterator)}

    handler = schemaless_method_handler(total, path="/Calc/Total")
    assert handler.stream_unary is not None

    requests = iter(ParseDict({"n": n}, Struct()) for n in (1, 2, 3))
    response = handler.stream_unary(requests, mocker.Mock())
    assert MessageToDict(response) == {"total": 6.0}


def test_stream_stream_flavor(mocker):
    """Both streaming signals combine into a stream-stream registration."""

    def echo(request_iterator):
        for item in request_iterator:
            yield {"echo": item["value"]}

    handler = schemaless_method_handler(echo, path="/Echo/Back")
    assert handler.stream_stream is not None

    requests = iter(ParseDict({"value": v}, Struct()) for v in ("a", "b"))
    echoes = [MessageToDict(r)["echo"] for r in handler.stream_stream(requests, mocker.Mock())]
    assert echoes == ["a", "b"]


def test_schemaless_rpc_registers(tmp_path):
    """The decorator registers under (service, method) and returns func unchanged."""

    def say(request):
        return {}

    app = SimpleGRPC(service_path=tmp_path / "services")
    assert app.schemaless_rpc("Greeter", "SayHello")(say) is say
    assert app._schemaless_handlers["Greeter"]["SayHello"] is say


def test_start_skips_compile_without_proto_handlers(tmp_path, mocker):
    """A pure-schemaless app never runs protoc; generic handlers are wired."""
    spy = mocker.patch("simple_grpc.applications.grpc.server")
    compile_spy = mocker.patch.object(SimpleGRPC, "_compile")
    app = SimpleGRPC(service_path=tmp_path / "services")

    @app.schemaless_rpc("Greeter", "SayHello")
    def say(request):
        return {"message": request["name"]}

    app.run()

    compile_spy.assert_not_called()
    spy.return_value.start.assert_called_once()
    (generic_handler,) = spy.return_value.add_generic_rpc_handlers.call_args.args[0]
    assert generic_handler.service_name() == "Greeter"
    assert "/Greeter/SayHello" in generic_handler._method_handlers


def test_start_wires_both_kinds(tmp_path, mocker):
    """Proto-backed and schemaless endpoints register on the same server."""
    proto_dir = tmp_path / "protos"
    proto_dir.mkdir()
    (proto_dir / "calc.proto").write_text(
        'syntax = "proto3";\n'
        "service Calc {\n"
        "  rpc Add (AddRequest) returns (AddReply);\n"
        "}\n"
        "message AddRequest { int64 a = 1; int64 b = 2; }\n"
        "message AddReply { int64 sum = 1; }\n"
    )
    (tmp_path / "services").mkdir()
    spy = mocker.patch("simple_grpc.applications.grpc.server")
    app = SimpleGRPC(proto_path=proto_dir, service_path=tmp_path / "services")

    @app.rpc("Calc", "Add")
    def add(request):
        return {"sum": request.a + request.b}

    @app.schemaless_rpc("Greeter", "SayHello")
    def say(request):
        return {"message": request["name"]}

    app.run()

    spy.return_value.add_insecure_port.assert_called_once()  # compile ran fine
    assert app._schemaless_handlers["Greeter"]["SayHello"] is say


def test_no_proto_files_error_suggests_schemaless(tmp_path):
    """The no-proto-files error points at both fixes (proto file or schemaless_rpc)."""
    proto_dir = tmp_path / "protos"
    proto_dir.mkdir()
    (tmp_path / "services").mkdir()
    app = SimpleGRPC(proto_path=proto_dir, service_path=tmp_path / "services")

    @app.rpc("Calc", "Add")
    def add(request):
        return {"sum": request.a + request.b}

    with pytest.raises(RuntimeError, match=r"no proto files.*schemaless_rpc"):
        app._compile()


def test_end_to_end_over_real_channel(tmp_path):
    """Full pipeline: a real server serves schemaless RPCs to a real channel."""
    app = SimpleGRPC(service_path=tmp_path / "services", port=0)

    @app.schemaless_rpc("Greeter", "SayHello")
    def say(request):
        return {"message": f"Hello, {request['name']}!"}

    @app.schemaless_rpc("Greeter", "CountUp")
    def count_up(request):
        for n in range(1, int(request["up_to"]) + 1):
            yield {"n": n}

    server = grpc.server(ThreadPoolExecutor())
    app._register_schemaless(server)
    port = server.add_insecure_port("localhost:0")
    server.start()
    try:
        with grpc.insecure_channel(f"localhost:{port}") as channel:
            say = channel.unary_unary(
                "/Greeter/SayHello",
                request_serializer=Struct.SerializeToString,
                response_deserializer=Struct.FromString,
            )
            reply = MessageToDict(say(ParseDict({"name": "wire"}, Struct())))
            assert reply["message"] == "Hello, wire!"

            count_up_call = channel.unary_stream(
                "/Greeter/CountUp",
                request_serializer=Struct.SerializeToString,
                response_deserializer=Struct.FromString,
            )
            nums = [MessageToDict(r)["n"] for r in count_up_call(ParseDict({"up_to": 3}, Struct()))]
            assert nums == [1.0, 2.0, 3.0]
    finally:
        server.stop(grace=None)
