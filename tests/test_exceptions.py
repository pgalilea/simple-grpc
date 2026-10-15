import grpc

from simple_grpc import exceptions as exc


def test_one_exception_per_status_code() -> None:
    names = [name for name in exc.__all__ if name != "GRPCException"]
    codes = [status for status in grpc.StatusCode if status is not grpc.StatusCode.OK]
    assert len(names) == len(codes) == len(set(names))


def test_exception_carries_code_and_details(mocker) -> None:
    err = exc.NotFoundError("user not found")
    assert err.code is grpc.StatusCode.NOT_FOUND
    assert err.details == "user not found"
    assert isinstance(err, exc.GRPCException)
