"""Tests for the optional gRPC server reflection exposure (``reflection=True``)."""

from concurrent.futures import ThreadPoolExecutor

import grpc
from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc
import pytest

from simple_grpc import SimpleGRPC


def _list_services(port: int) -> list[str]:
    """Query a real server's reflection service; return the advertised names."""
    with grpc.insecure_channel(f"localhost:{port}") as channel:
        stub = reflection_pb2_grpc.ServerReflectionStub(channel)
        request = reflection_pb2.ServerReflectionRequest(list_services="")
        responses = stub.ServerReflectionInfo(iter([request]))  # type: ignore[reportCallIssue]
        return [
            svc.name for response in responses for svc in response.list_services_response.service
        ]


def _serve_reflection(app: SimpleGRPC) -> tuple[int, grpc.Server]:
    """Register reflection on a real server like ``_start`` does; keep server referenced."""
    server = grpc.server(ThreadPoolExecutor())
    service_names = [*app._schemaless_handlers]
    if app.health:
        app._register_health(server)
        service_names.append("grpc.health.v1.Health")
    if app.reflection:
        app._register_reflection(server, service_names)
    port = server.add_insecure_port("localhost:0")
    server.start()
    return port, server


def test_reflection_lists_schemaless_service():
    app = SimpleGRPC(reflection=True, port=0)

    @app.schemaless_rpc("Greeter", "SayHello")
    def say_hello(request):  # pragma: no cover - not invoked, only advertised
        """Register a handler so reflection has a service to advertise."""
        return {"message": "hi"}

    port, _server = _serve_reflection(app)

    names = _list_services(port)
    assert "Greeter" in names


def test_reflection_advertises_health_service():
    app = SimpleGRPC(reflection=True, health=True, port=0)
    port, _server = _serve_reflection(app)

    names = _list_services(port)
    assert "grpc.health.v1.Health" in names


def test_reflection_disabled_is_unimplemented():
    app = SimpleGRPC(reflection=False, port=0)
    port, _server = _serve_reflection(app)

    with grpc.insecure_channel(f"localhost:{port}") as channel:
        stub = reflection_pb2_grpc.ServerReflectionStub(channel)
        with pytest.raises(grpc.RpcError) as exc:
            stub.ServerReflectionInfo(
                iter([reflection_pb2.ServerReflectionRequest(list_services="")]),  # type: ignore[reportCallIssue]
                timeout=2,
            ).next()
        assert exc.value.code() == grpc.StatusCode.UNIMPLEMENTED
