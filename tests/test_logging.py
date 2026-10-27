"""Tests for simple_grpc logging: startup banner, access lines, and setup."""

import logging

import grpc
import pytest

from simple_grpc import Depends, SimpleGRPC, logging as sg_logging
from simple_grpc.exceptions import NotFoundError, UnauthenticatedError
from simple_grpc.logging import LoggingConfig


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


@pytest.fixture
def clean_loggers():
    """Reset the framework loggers around a test so user state never leaks."""
    saved = [
        (lg.handlers[:], lg.level, lg.propagate, lg.disabled)
        for lg in (sg_logging.logger, sg_logging.access_logger)
    ]
    for lg in (sg_logging.logger, sg_logging.access_logger):
        lg.handlers.clear()
    yield
    for lg, (handlers, level, propagate, disabled) in zip(
        (sg_logging.logger, sg_logging.access_logger), saved, strict=True
    ):
        lg.handlers[:] = handlers
        lg.level = level
        lg.propagate = propagate
        lg.disabled = disabled


def rendered(mock_logger):
    """All INFO messages a mocked logger received, with args interpolated."""
    return [call.args[0] % call.args[1:] for call in mock_logger.info.call_args_list]


def test_start_logs_banner(app, mocker):
    """_start logs port, protos, service directory, and services."""

    @app.rpc("Echo", "Say")
    def say(request, context):
        return {"message": "ok"}

    mocker.patch.object(grpc, "server", return_value=mocker.Mock())
    err = mocker.patch("simple_grpc.applications.logger")

    app._start()

    lines = "\n".join(rendered(err))
    assert "Starting simple_grpc (pid" in lines
    assert "1 proto file(s) from '" in lines and "protos" in lines
    assert "services" in lines  # service directory
    assert "Registered services: Echo (Say)" in lines
    assert "Serving on" in lines


def test_start_logs_tls_when_configured(app, mocker):
    """With credentials the banner says (TLS) instead of (No TLS)."""
    mocker.patch.object(grpc, "server", return_value=mocker.Mock())
    err = mocker.patch("simple_grpc.applications.logger")
    app.credentials = mocker.Mock(to_server_credentials=mocker.Mock())

    app._start()

    assert any("(TLS)" in line for line in rendered(err))


def test_access_log_success(app, mocker):
    """A unary RPC logs its peer, method path, and OK status."""
    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]

    @app.rpc("Echo", "Say")
    def say(request, context):
        return {"message": request.message}

    servicer = app._build_servicer("Echo", pb2, pb2_grpc)
    access = mocker.patch("simple_grpc.logging.access_logger", disabled=False)
    context = mocker.Mock()
    context.peer.return_value = "ipv4:1.2.3.4:5"

    servicer.Say(pb2.EchoRequest(message="hi"), context)

    assert rendered(access) == ['ipv4:1.2.3.4:5 - "POST /demo.Echo/Say HTTP/2.0" OK']


def test_access_log_grpc_exception(app, mocker):
    """A GRPCException logs its status code before aborting."""

    @app.rpc("Echo", "Say")
    def say(request, context):
        raise NotFoundError("no user 5")

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    servicer = app._build_servicer("Echo", pb2, pb2_grpc)
    access = mocker.patch("simple_grpc.logging.access_logger", disabled=False)
    context = mocker.Mock()
    context.abort.side_effect = RuntimeError("abort raises")  # like real grpc

    with pytest.raises(RuntimeError):
        servicer.Say(pb2.EchoRequest(), context)

    assert rendered(access) == [f'{context.peer()} - "POST /demo.Echo/Say HTTP/2.0" NOT_FOUND']


def test_access_log_internal_exception(app, mocker):
    """A plain exception logs INTERNAL and is hidden from the client."""
    mocker.patch("simple_grpc.dispatch.logger")

    @app.rpc("Echo", "Say")
    def say(request, context):
        raise RuntimeError("boom")

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    servicer = app._build_servicer("Echo", pb2, pb2_grpc)
    access = mocker.patch("simple_grpc.logging.access_logger", disabled=False)
    context = mocker.Mock()
    context.code.return_value = None
    context.details.return_value = None

    with pytest.raises(RuntimeError, match="boom"):
        servicer.Say(pb2.EchoRequest(), context)

    lines = rendered(access)
    assert len(lines) == 1
    assert lines[0].endswith('"POST /demo.Echo/Say HTTP/2.0" INTERNAL')
    context.abort.assert_called_once_with(grpc.StatusCode.INTERNAL, "Internal server error")


def test_access_log_abort_code(app, mocker):
    """A handler calling context.abort logs the real status code, not UNKNOWN."""

    @app.rpc("Echo", "Say")
    def say(request, context):
        context.abort(grpc.StatusCode.NOT_FOUND, "gone")

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    servicer = app._build_servicer("Echo", pb2, pb2_grpc)
    access = mocker.patch("simple_grpc.logging.access_logger", disabled=False)
    context = mocker.Mock()
    context.abort.side_effect = RuntimeError("abort raises")  # like real grpc
    context.code.return_value = grpc.StatusCode.NOT_FOUND

    with pytest.raises(RuntimeError, match="abort raises"):
        servicer.Say(pb2.EchoRequest(), context)

    lines = rendered(access)
    assert len(lines) == 1
    assert lines[0].endswith('"POST /demo.Echo/Say HTTP/2.0" NOT_FOUND')


def test_access_log_dependency_grpc_exception(app, mocker):
    """A GRPCException from a dependency logs and aborts with its code."""

    def forbidden(context):
        raise UnauthenticatedError("no token")

    @app.rpc("Echo", "Say")
    def say(request, ok=Depends(forbidden)):
        return {"message": "should-not-happen"}

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    servicer = app._build_servicer("Echo", pb2, pb2_grpc)
    access = mocker.patch("simple_grpc.logging.access_logger", disabled=False)
    context = mocker.Mock()
    context.abort.side_effect = RuntimeError("abort raises")  # like real grpc

    with pytest.raises(RuntimeError):
        servicer.Say(pb2.EchoRequest(), context)

    context.abort.assert_called_once_with(UnauthenticatedError.code, "no token")
    lines = rendered(access)
    assert len(lines) == 1
    assert lines[0].endswith('"POST /demo.Echo/Say HTTP/2.0" UNAUTHENTICATED')


def test_access_log_streaming(app, mocker):
    """A streaming RPC logs OK once gRPC consumes the whole stream."""
    items = iter([None, None])
    access = mocker.patch("simple_grpc.logging.access_logger", disabled=False)
    context = mocker.Mock()
    context.peer.return_value = "ipv4:1.2.3.4:5"

    out = list(SimpleGRPC._stream(items, mocker.sentinel.cls, context, [], path="/demo.Echo/Say"))

    assert len(out) == 2
    assert rendered(access) == ['ipv4:1.2.3.4:5 - "POST /demo.Echo/Say HTTP/2.0" OK']


def test_formatter_colors_by_level():
    """ColourFormatter styles the level prefix with ANSI when colors are on."""
    fmt = sg_logging.ColourFormatter("%(levelprefix)s %(message)s", use_colors=True)
    record = logging.LogRecord("simple_grpc.error", logging.INFO, __file__, 1, "hello", (), None)

    out = fmt.format(record)

    assert "\x1b[" in out and "INFO:" in out and "hello" in out


def test_formatter_plain_without_colors():
    """ColourFormatter emits no ANSI codes when colors are off."""
    fmt = sg_logging.ColourFormatter("%(levelprefix)s %(message)s", use_colors=False)
    record = logging.LogRecord("simple_grpc.error", logging.ERROR, __file__, 1, "boom", (), None)

    out = fmt.format(record)

    assert "\x1b" not in out and "ERROR:" in out and "boom" in out


def test_configure_logging_is_idempotent(clean_loggers):
    """A second configure call leaves exactly one handler per logger."""
    sg_logging.configure_logging(LoggingConfig())
    first = (len(sg_logging.logger.handlers), len(sg_logging.access_logger.handlers))

    sg_logging.configure_logging(LoggingConfig())

    assert first == (1, 1)
    assert sg_logging.logger.level == logging.INFO
    assert not sg_logging.logger.propagate


def test_configure_logging_owns_its_loggers(clean_loggers):
    """Pre-existing handlers on the framework loggers are replaced."""
    sg_logging.logger.addHandler(logging.NullHandler())

    sg_logging.configure_logging(LoggingConfig())

    assert len(sg_logging.logger.handlers) == 1
    assert isinstance(sg_logging.logger.handlers[0], logging.StreamHandler)
    assert len(sg_logging.access_logger.handlers) == 1


def test_configure_logging_applies_level(clean_loggers):
    """log_level drives both loggers."""
    sg_logging.configure_logging(LoggingConfig(log_level="warning"))

    assert sg_logging.logger.level == logging.WARNING
    assert sg_logging.access_logger.level == logging.WARNING


def test_configure_logging_applies_access_switch(clean_loggers):
    """access_log drives the access logger's stdlib disabled flag, both ways."""
    sg_logging.configure_logging(LoggingConfig(access_log=False))
    assert sg_logging.access_logger.disabled is True

    sg_logging.configure_logging(LoggingConfig(access_log=True))
    assert sg_logging.access_logger.disabled is False
    assert not sg_logging.logger.disabled


def test_logging_config_defaults():
    """Defaults: access on, INFO, auto-detected colors."""
    config = LoggingConfig()

    assert config.access_log is True
    assert config.log_level == logging.INFO
    assert isinstance(config.use_colors, bool)


def test_logging_config_accepts_int_level():
    """An int log_level passes through untouched."""
    assert LoggingConfig(log_level=logging.DEBUG).log_level == logging.DEBUG


def test_logging_config_invalid_level_raises():
    """An unknown level name fails fast with the valid names listed."""
    with pytest.raises(ValueError, match="invalid log_level 'loud'"):
        LoggingConfig(log_level="loud")


def test_logging_config_forced_colors():
    """use_colors=True/False skips the TTY auto-detection."""
    assert LoggingConfig(use_colors=True).use_colors is True
    assert LoggingConfig(use_colors=False).use_colors is False


def test_style_forced_colors():
    """style() emits ANSI only when the resolved colors flag is on."""
    assert "\x1b[" in sg_logging.style("No", True, bold=True)
    assert sg_logging.style("No", False, bold=True) == "No"


def test_access_log_disabled(app, mocker):
    """A disabled access logger silences per-RPC lines entirely."""

    @app.rpc("Echo", "Say")
    def say(request, context):
        return {"message": "x"}

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    servicer = app._build_servicer("Echo", pb2, pb2_grpc)
    access = mocker.patch("simple_grpc.logging.access_logger", disabled=True)

    servicer.Say(pb2.EchoRequest(), mocker.Mock())

    access.info.assert_not_called()


def test_access_log_disabled_streaming(app, mocker):
    """A disabled access logger also silences the streaming completion line."""
    access = mocker.patch("simple_grpc.logging.access_logger", disabled=True)

    out = list(SimpleGRPC._stream(iter([None]), mocker.sentinel.cls, mocker.Mock(), [], path="/x"))

    assert out == [None]
    access.info.assert_not_called()
