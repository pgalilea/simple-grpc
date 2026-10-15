"""Exceptions for every gRPC status code (see grpc.StatusCode)."""

import grpc


class GRPCException(Exception):
    """Base exception carrying the gRPC status code and details."""

    code: grpc.StatusCode = grpc.StatusCode.UNKNOWN

    def __init__(self, details: str = "") -> None:
        super().__init__(details)
        self.details = details or self.code.value[1]


class CancelledError(GRPCException):
    """Raised for gRPC status CANCELLED."""

    code = grpc.StatusCode.CANCELLED


class UnknownError(GRPCException):
    """Raised for gRPC status UNKNOWN."""

    code = grpc.StatusCode.UNKNOWN


class InvalidArgumentError(GRPCException):
    """Raised for gRPC status INVALID_ARGUMENT."""

    code = grpc.StatusCode.INVALID_ARGUMENT


class DeadlineExceededError(GRPCException):
    """Raised for gRPC status DEADLINE_EXCEEDED."""

    code = grpc.StatusCode.DEADLINE_EXCEEDED


class NotFoundError(GRPCException):
    """Raised for gRPC status NOT_FOUND."""

    code = grpc.StatusCode.NOT_FOUND


class AlreadyExistsError(GRPCException):
    """Raised for gRPC status ALREADY_EXISTS."""

    code = grpc.StatusCode.ALREADY_EXISTS


class PermissionDeniedError(GRPCException):
    """Raised for gRPC status PERMISSION_DENIED."""

    code = grpc.StatusCode.PERMISSION_DENIED


class ResourceExhaustedError(GRPCException):
    """Raised for gRPC status RESOURCE_EXHAUSTED."""

    code = grpc.StatusCode.RESOURCE_EXHAUSTED


class FailedPreconditionError(GRPCException):
    """Raised for gRPC status FAILED_PRECONDITION."""

    code = grpc.StatusCode.FAILED_PRECONDITION


class AbortedError(GRPCException):
    """Raised for gRPC status ABORTED."""

    code = grpc.StatusCode.ABORTED


class OutOfRangeError(GRPCException):
    """Raised for gRPC status OUT_OF_RANGE."""

    code = grpc.StatusCode.OUT_OF_RANGE


class UnimplementedError(GRPCException):
    """Raised for gRPC status UNIMPLEMENTED."""

    code = grpc.StatusCode.UNIMPLEMENTED


class InternalServerError(GRPCException):
    """Raised for gRPC status INTERNAL."""

    code = grpc.StatusCode.INTERNAL


class UnavailableError(GRPCException):
    """Raised for gRPC status UNAVAILABLE."""

    code = grpc.StatusCode.UNAVAILABLE


class DataLossError(GRPCException):
    """Raised for gRPC status DATA_LOSS."""

    code = grpc.StatusCode.DATA_LOSS


class UnauthenticatedError(GRPCException):
    """Raised for gRPC status UNAUTHENTICATED."""

    code = grpc.StatusCode.UNAUTHENTICATED


__all__ = [
    "GRPCException",
    "AbortedError",
    "AlreadyExistsError",
    "CancelledError",
    "DataLossError",
    "DeadlineExceededError",
    "FailedPreconditionError",
    "InternalServerError",
    "InvalidArgumentError",
    "NotFoundError",
    "OutOfRangeError",
    "PermissionDeniedError",
    "ResourceExhaustedError",
    "UnavailableError",
    "UnauthenticatedError",
    "UnimplementedError",
    "UnknownError",
]
