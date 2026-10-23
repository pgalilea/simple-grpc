"""Tests for the optional grpc.health.v1.Health exposure (``health=True``)."""

from concurrent.futures import ThreadPoolExecutor

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
import pytest

from simple_grpc import SimpleGRPC


def _serve_health(app: SimpleGRPC) -> tuple[int, grpc.Server]:
    """Register health on a real server; return (port, server) - keep server referenced."""
    server = grpc.server(ThreadPoolExecutor())
    if app.health:
        app._register_health(server)
    port = server.add_insecure_port("localhost:0")
    server.start()
    return port, server


def test_health_serving_over_real_channel():
    app = SimpleGRPC(health=True, port=0)
    port, _server = _serve_health(app)

    with grpc.insecure_channel(f"localhost:{port}") as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        reply = stub.Check(health_pb2.HealthCheckRequest(service=""))
        assert reply.status == health_pb2.HealthCheckResponse.SERVING


def test_health_not_serving_reflects_status_change():
    """The servicer stays reachable via app.health_servicer for status flips."""
    app = SimpleGRPC(health=True, port=0)
    port, _server = _serve_health(app)
    stub = health_pb2_grpc.HealthStub(grpc.insecure_channel(f"localhost:{port}"))

    assert (
        stub.Check(health_pb2.HealthCheckRequest()).status == health_pb2.HealthCheckResponse.SERVING
    )

    app.health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
    assert (
        stub.Check(health_pb2.HealthCheckRequest()).status
        == health_pb2.HealthCheckResponse.NOT_SERVING
    )


def test_health_watch_pushes_status_changes():
    """Watch streams the current status and every subsequent change."""
    app = SimpleGRPC(health=True, port=0)
    port, _server = _serve_health(app)
    stub = health_pb2_grpc.HealthStub(grpc.insecure_channel(f"localhost:{port}"))

    call = stub.Watch(health_pb2.HealthCheckRequest(service=""), None)

    first = next(call)
    assert first.status == health_pb2.HealthCheckResponse.SERVING

    app.health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
    assert next(call).status == health_pb2.HealthCheckResponse.NOT_SERVING
    call.cancel()


def test_health_disabled_is_unimplemented():
    app = SimpleGRPC(health=False, port=0)
    port, _server = _serve_health(app)

    with grpc.insecure_channel(f"localhost:{port}") as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        with pytest.raises(grpc.RpcError) as exc:
            stub.Check(health_pb2.HealthCheckRequest(), timeout=2)
        assert exc.value.code() == grpc.StatusCode.UNIMPLEMENTED


def test_health_servicer_none_before_start_and_when_disabled():
    assert SimpleGRPC(health=True, port=0).health_servicer is None
    assert SimpleGRPC(health=False, port=0).health_servicer is None
