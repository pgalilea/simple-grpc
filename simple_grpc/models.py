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


@dataclass
class ServerCredentials:
    """
    TLS material for the server, passed to ``grpc.ssl_server_credentials``.

    Each PEM field accepts either a ``Path`` to a file or the PEM content
    itself (``str`` or ``bytes``); everything is resolved to bytes eagerly at
    construction so a bad path or non-PEM input fails fast, not at bind time.
    """

    private_key: str | bytes | Path
    certificate_chain: str | bytes | Path
    root_certificates: str | bytes | Path | None = None
    require_client_auth: bool = False

    def __post_init__(self) -> None:
        """
        Resolve every PEM field to bytes, failing fast on bad input.

        Raises:
            ValueError: If a ``Path`` is not a readable file, if a field is
                not PEM material (catches e.g. a path passed as ``str``), or
                if ``require_client_auth`` is set without
                ``root_certificates``.
        """
        if self.require_client_auth and self.root_certificates is None:
            raise ValueError(
                "require_client_auth=True requires root_certificates (the CA bundle verifying client certs)."
            )
        self.private_key = self._pem_bytes(self.private_key, "private_key")
        self.certificate_chain = self._pem_bytes(self.certificate_chain, "certificate_chain")
        self.root_certificates = (
            None
            if self.root_certificates is None
            else self._pem_bytes(self.root_certificates, "root_certificates")
        )

    @staticmethod
    def _pem_bytes(value: str | bytes | Path, field: str) -> bytes:
        """
        Resolve one PEM field to bytes.

        Args:
            value: A ``Path`` to a PEM file, or PEM content (``str``/``bytes``).
            field: Field name, used in error messages.

        Returns:
            The PEM bytes.

        Raises:
            ValueError: If the ``Path`` is not a readable file, or the
                resolved value is empty or not PEM material.
        """
        if isinstance(value, Path):
            try:
                value = value.read_bytes()
            except OSError as exc:
                raise ValueError(f"{field}: cannot read '{value}' ({exc}).") from exc
        if isinstance(value, str):
            value = value.encode()
        if b"-----BEGIN" not in value:
            raise ValueError(
                f"{field} must be PEM content (str/bytes) or a Path to a PEM file; "
                f"got {value[:60]!r}..."
            )
        return value

    def to_server_credentials(self) -> grpc.ServerCredentials:
        """
        Build the credentials object for ``grpc.Server.add_secure_port``.

        Returns:
            The ``grpc.ServerCredentials`` built by
            ``grpc.ssl_server_credentials`` from the resolved PEM bytes.
        """
        return grpc.ssl_server_credentials(
            private_key_certificate_chain_pairs=[(self.private_key, self.certificate_chain)],
            root_certificates=self.root_certificates,
            require_client_auth=self.require_client_auth,
        )


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
