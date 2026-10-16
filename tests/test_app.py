import inspect

import grpc
import pytest

from simple_grpc import SimpleGRPC
from simple_grpc.applications import ServerOptions
from simple_grpc.models import ProtoConfig


PROTO = """
syntax = "proto3";
package demo;

message EchoRequest {
  string message = 1;
}

message EchoResponse {
  string message = 1;
}

service Echo {
  rpc Say(EchoRequest) returns (EchoResponse);
}
"""


@pytest.fixture
def app(tmp_path):
    proto_dir = tmp_path / "protos"
    proto_dir.mkdir()
    (proto_dir / "demo.proto").write_text(PROTO)
    (tmp_path / "services").mkdir()  # service_path must exist (see _compile)
    return SimpleGRPC(
        proto_path=proto_dir,
        service_path=tmp_path / "services",
    )


def test_compile_and_load(app):
    app._compile()
    assert (app.proto_config.service_path / "demo_pb2.py").exists()
    assert (app.proto_config.service_path / "demo_pb2_grpc.py").exists()

    mods = app._load_generated()
    pb2, pb2_grpc = mods["demo"]
    assert hasattr(pb2, "EchoRequest")
    assert hasattr(pb2_grpc, "EchoServicer")


def test_rpc_dispatch(app, mocker):
    @app.rpc("Echo", "Say")
    def say(request, context):
        return {"message": request.message}

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    servicer = app._build_servicer("Echo", pb2, pb2_grpc)

    response = servicer.Say(pb2.EchoRequest(message="hi"), mocker.Mock())
    assert isinstance(response, pb2.EchoResponse)
    assert response.message == "hi"


def test_message_passthrough(app, mocker):
    @app.rpc("Echo", "Say")
    def say(request, context):
        return pb2.EchoResponse(message="raw")  # noqa: B023

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    servicer = app._build_servicer("Echo", pb2, pb2_grpc)

    response = servicer.Say(pb2.EchoRequest(message="hi"), mocker.Mock())
    assert response.message == "raw"


def test_request_only_injection(app, mocker):
    @app.rpc("Echo", "Say")
    def say(request):
        return {"message": "personal" if not request.message else request.message}

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    servicer = app._build_servicer("Echo", pb2, pb2_grpc)

    response = servicer.Say(pb2.EchoRequest(message="hi"), mocker.Mock())
    assert response.message == "hi"


def test_context_only_injection(app, mocker):
    @app.rpc("Echo", "Say")
    def say(context):
        return {"message": "ctx-is-real" if context else ""}

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    servicer = app._build_servicer("Echo", pb2, pb2_grpc)

    response = servicer.Say(pb2.EchoRequest(), mocker.Mock())
    assert response.message == "ctx-is-real"


def test_no_params_handler(app, mocker):
    @app.rpc("Echo", "Say")
    def say():
        return {"message": "bare"}

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    servicer = app._build_servicer("Echo", pb2, pb2_grpc)

    response = servicer.Say(pb2.EchoRequest(), mocker.Mock())
    assert response.message == "bare"


def test_request_iterator_alias(app, mocker):
    @app.rpc("Echo", "Say")
    def say(request_iterator, context=None):
        assert request_iterator is context
        return {"message": "alias"}

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    servicer = app._build_servicer("Echo", pb2, pb2_grpc)

    request = pb2.EchoRequest()
    response = servicer.Say(request, request)
    assert response.message == "alias"


def test_unknown_param_raises(app):
    @app.rpc("Echo", "Say")
    def say(req):
        return {}

    app._compile()
    with pytest.raises(ValueError, match="declares unregistrable parameter"):
        app._build_servicer("Echo", *app._load_generated()["demo"])


def test_unknown_method_raises(app):
    @app.rpc("Echo", "Nope")
    def nope(request, context):
        return {}

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    with pytest.raises(ValueError, match=r"Nope.*Available:.*Say"):
        app._build_servicer("Echo", pb2, pb2_grpc)


def test_unknown_service_raises(app, mocker):
    @app.rpc("Missing", "Say")
    def say(request, context):
        return {}

    app._compile()
    mods = app._load_generated()
    with pytest.raises(ValueError, match=r"'Missing'.*Available:.*Echo"):
        app._register_services(mods, mocker.Mock())


def test_dev_wires_everything(app, mocker):
    server = mocker.Mock()
    mocker.patch.object(grpc, "server", return_value=server)

    @app.rpc("Echo", "Say")
    def say(request, context):
        return {"message": "ok"}

    app.dev()

    server.add_insecure_port.assert_called_once_with("[::]:50051")
    server.start.assert_called_once()
    # the generated add_EchoServicer_to_server registered a handler on the server
    (handler,) = server.add_generic_rpc_handlers.call_args.args[0]
    assert "/demo.Echo/Say" in handler._method_handlers


def test_invalid_proto_name_raises(tmp_path):
    proto_dir = tmp_path / "protos"
    proto_dir.mkdir()
    with pytest.raises(ValueError, match="my-service.proto"):
        ProtoConfig(proto_path=proto_dir, proto_files=["my-service.proto"], service_path=tmp_path)


def test_server_options_stay_in_sync_with_grpc():
    grpc_params = set(inspect.signature(grpc.server).parameters) - {"thread_pool"}
    assert set(ServerOptions.__annotations__) == grpc_params


def test_server_options_forwarded(tmp_path, mocker):
    proto_dir = tmp_path / "protos"
    proto_dir.mkdir()
    (proto_dir / "demo.proto").write_text(PROTO)
    (tmp_path / "services").mkdir()
    spy = mocker.patch("simple_grpc.applications.grpc.server")
    SimpleGRPC(
        proto_path=proto_dir,
        service_path=tmp_path / "services",
        options=[("grpc.max_receive_message_length", 1)],
        compression=grpc.Compression.Gzip,
    )._start()
    kwargs = spy.call_args.kwargs
    assert kwargs["options"] == [("grpc.max_receive_message_length", 1)]
    assert kwargs["compression"] == grpc.Compression.Gzip
