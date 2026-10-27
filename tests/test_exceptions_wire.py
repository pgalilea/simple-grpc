import pytest

from simple_grpc import Depends, SimpleGRPC
from simple_grpc.exceptions import NotFoundError


class _UnusedResponse:
    """Never instantiated: the test handlers only raise."""


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


def test_non_grpc_exception_not_converted(mocker):
    """Plain exceptions still propagate for grpcio's UNKNOWN mapping."""
    expected = RuntimeError("boom")

    def handler(request, context):
        raise expected

    method = SimpleGRPC._wrap_handler(handler, _UnusedResponse, ())
    context = mocker.Mock()

    with pytest.raises(RuntimeError) as err:
        method(request=mocker.sentinel.req, context=context)

    assert err.value is expected
    context.abort.assert_not_called()
