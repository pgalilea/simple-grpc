import grpc
import pytest

from simple_grpc import Depends, SimpleGRPC
from simple_grpc.exceptions import NotFoundError


class _UnusedResponse:
    """Never instantiated: the test handlers only raise."""


class _Abort(Exception):
    """Raised by _Context.abort, like grpc's abort sentinel."""


class _Context:
    """Minimal ServicerContext with real code and details state."""

    def __init__(self) -> None:
        self._code: grpc.StatusCode | None = None
        self._details: bytes | None = None

    def code(self) -> grpc.StatusCode | None:
        return self._code

    def details(self) -> bytes | None:
        return self._details

    def set_code(self, code: grpc.StatusCode) -> None:
        self._code = code

    def set_details(self, details: str) -> None:
        self._details = details.encode()

    def abort(self, code: grpc.StatusCode, details: str) -> None:
        self.set_code(code)
        self.set_details(details)
        raise _Abort()

    def peer(self) -> str:
        return "test-peer"


def test_grpc_exception_aborts_with_code_and_details(mocker):
    """A raised GRPCException reaches context.abort with its wire status."""

    def handler(request, context):
        raise NotFoundError("no user 5")

    method = SimpleGRPC._wrap_handler(handler, _UnusedResponse, ())
    context = mocker.Mock()
    context.abort.side_effect = RuntimeError("abort raises")  # like real grpc

    with pytest.raises(RuntimeError):
        method(request=mocker.sentinel.req, context=context)

    context.abort.assert_called_once_with(NotFoundError.code, "no user 5")

def test_grpc_exception_teardown_still_runs(mocker):
    """Generator dependency teardown runs even when the handler aborts."""
    ran = []

    def teardown() -> ...:
        yield
        ran.append(True)

    def handler(request, context, dep=Depends(teardown)):
        raise NotFoundError("gone")

    method = SimpleGRPC._wrap_handler(handler, _UnusedResponse, ())
    context = mocker.Mock()
    context.abort.side_effect = RuntimeError("abort raises")

    with pytest.raises(RuntimeError):
        method(request=mocker.sentinel.req, context=context)

    assert ran == [True]


def test_teardown_failure_does_not_mask_handler_error(mocker):
    """A failing teardown does not replace the original RPC error."""
    mocker.patch("simple_grpc.dispatch.logger")

    def teardown() -> ...:
        yield
        raise RuntimeError("cleanup failed")

    def handler(request, context, dep=Depends(teardown)):
        raise NotFoundError("gone")

    method = SimpleGRPC._wrap_handler(handler, _UnusedResponse, ())
    context = mocker.Mock()
    context.abort.side_effect = RuntimeError("abort raises")

    with pytest.raises(RuntimeError, match="abort raises"):
        method(request=mocker.sentinel.req, context=context)

    context.abort.assert_called_once_with(NotFoundError.code, "gone")


def test_unexpected_exception_aborts_internal_and_is_logged(mocker):
    """Unexpected exceptions are logged and hidden behind INTERNAL."""
    log = mocker.patch("simple_grpc.dispatch.logger")

    def handler(request, context):
        raise RuntimeError("secret database password")

    method = SimpleGRPC._wrap_handler(handler, _UnusedResponse, ())
    context = _Context()

    with pytest.raises(_Abort):
        method(request=mocker.sentinel.req, context=context)

    assert context.code() == grpc.StatusCode.INTERNAL
    assert context.details() == b"Internal server error"
    log.error.assert_called_once()
    assert log.error.call_args.kwargs["exc_info"] is not None


def test_unexpected_dependency_exception_is_hidden(mocker):
    """Raw dependency errors get the same INTERNAL treatment."""
    log = mocker.patch("simple_grpc.dispatch.logger")

    def dep():
        raise RuntimeError("secret token")

    def handler(request, context, value=Depends(dep)):
        return None

    method = SimpleGRPC._wrap_handler(handler, _UnusedResponse, ())
    context = _Context()

    with pytest.raises(_Abort):
        method(request=mocker.sentinel.req, context=context)

    assert context.code() == grpc.StatusCode.INTERNAL
    assert context.details() == b"Internal server error"
    log.error.assert_called_once()


def test_explicit_abort_details_are_preserved(mocker):
    """A user context.abort keeps its code and details."""
    log = mocker.patch("simple_grpc.dispatch.logger")

    def handler(request, context):
        context.abort(grpc.StatusCode.NOT_FOUND, "note missing")

    method = SimpleGRPC._wrap_handler(handler, _UnusedResponse, ())
    context = _Context()

    with pytest.raises(_Abort):
        method(request=mocker.sentinel.req, context=context)

    assert context.code() == grpc.StatusCode.NOT_FOUND
    assert context.details() == b"note missing"
    log.error.assert_not_called()
    log.info.assert_called_once()


def test_explicit_code_without_details_uses_safe_default(mocker):
    """An explicit code without details does not expose the exception text."""
    log = mocker.patch("simple_grpc.dispatch.logger")

    def handler(request, context):
        context.set_code(grpc.StatusCode.NOT_FOUND)
        raise RuntimeError("secret note 42")

    method = SimpleGRPC._wrap_handler(handler, _UnusedResponse, ())
    context = _Context()

    with pytest.raises(RuntimeError, match="secret note 42"):
        method(request=mocker.sentinel.req, context=context)

    assert context.code() == grpc.StatusCode.NOT_FOUND
    assert context.details() == b"not found"
    log.error.assert_called_once()


def test_teardown_failure_aborts_internal(mocker):
    """Teardown failures are logged and hidden behind INTERNAL."""
    log = mocker.patch("simple_grpc.dispatch.logger")

    def teardown() -> ...:
        yield
        raise RuntimeError("secret cleanup")

    def handler(request, context, dep=Depends(teardown)):
        return None

    method = SimpleGRPC._wrap_handler(handler, _UnusedResponse, ())
    context = _Context()

    with pytest.raises(_Abort):
        method(request=mocker.sentinel.req, context=context)

    assert context.code() == grpc.StatusCode.INTERNAL
    assert context.details() == b"Internal server error"
    log.error.assert_called_once()
