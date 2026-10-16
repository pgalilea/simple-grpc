from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import grpc


@dataclass
class ProtoConfig:
    """Where proto files live and how to compile them."""

    proto_path: Path
    proto_files: list[str]
    service_path: Path

    def __post_init__(self) -> None:
        """Resolve paths, discover protos, and validate module names.

        Raises:
            ValueError: If a proto stem is not a valid Python module name.
        """

        self.service_path = Path(self.service_path or "services").resolve()
        self.proto_path = Path(self.proto_path or "protos").resolve()

        if self.proto_files is None:
            self.proto_files = [pf.name for pf in self.proto_path.glob("*.proto")]
        else:
            self.proto_files = self.proto_files or []

        for pf in self.proto_files:
            if not Path(pf).stem.isidentifier():
                raise ValueError(
                    f"proto file '{pf!r}' needs a valid module name (got '{Path(pf).stem!r}')"
                )

    def to_compiler_args(self) -> list[str]:
        """Build the protoc argv, including the well-known-types include path.

        Returns:
            Argument list for ``protoc.main``.
        """
        return [
            "",
            f"--proto_path={self.proto_path}",
            f"--python_out={self.service_path}",
            f"--grpc_python_out={self.service_path}",
            *self.proto_files,
        ]


class ServerOptions(TypedDict, total=False):
    """
    Keyword args forwarded verbatim to ``grpc.server()`` in :meth:`SimpleGRPC._start`.

    Mirrors the ``grpc.server()`` signature minus ``thread_pool``, which stays a
    first-class ``SimpleGRPC`` parameter. Kept in sync mechanically by
    ``tests/test_app.py::test_server_options_stay_in_sync_with_grpc``.
    """

    handlers: Iterable[grpc.GenericRpcHandler]
    interceptors: Iterable[grpc.ServerInterceptor]
    # ChannelArgument only exists in grpc's stubs at type-check time.
    options: "Sequence[grpc.ChannelArgument]"  # type: ignore[reportAttributeAccessIssue]
    maximum_concurrent_rpcs: int | None
    compression: grpc.Compression | None
    xds: bool
