from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha1
from importlib.resources import files
from pathlib import Path
import tempfile
from typing import TypedDict

import grpc


@dataclass
class ProtoConfig:
    """Where proto files live and how to compile them."""

    proto_path: Path
    proto_files: list[str]
    service_path: Path | None = None
    raw_protos: dict[str, str] | None = None

    def __post_init__(self) -> None:
        """
        Resolve paths, write raw protos if given, discover protos, and validate module names.

        In raw mode, ``raw_protos`` (stem -> content) is materialized into a
        content-addressed directory under ``tempfile.gettempdir()``: identical
        content hashes to the identical directory, so separate processes (one
        server, one client) rendezvous on generated code with no coordination.

        Raises:
            ValueError: If a proto stem is not a valid Python module name.
        """

        if self.raw_protos:
            self._materialize_raw_protos()

        self.service_path = Path(
            self.service_path or ("grpc_services" if not self.raw_protos else self.proto_path)
        ).resolve()

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

    def _materialize_raw_protos(self) -> None:
        """
        Write ``raw_protos`` (stem -> content) into a content-addressed tmp dir.

        Sets ``proto_path``/``proto_files`` there too; ``service_path`` stays
        ``None`` if not explicitly given, so it defaults to the same dir.

        No wipe of the dir: different content hashes to a different directory
        and identical content rewrites identical bytes, so nothing inside it
        can go stale.
        """

        digest = sha1(
            "\0".join(
                f"{stem}\0{content}"
                for stem, content in sorted(self.raw_protos.items())  # type: ignore
            ).encode("utf-8")
        ).hexdigest()[:12]  # "\0" to avoid collisions when concat

        self.proto_path = Path(tempfile.gettempdir()) / f"simple_grpc_raw_{digest}"
        self.proto_files = sorted(f"{stem}.proto" for stem in self.raw_protos)  # type: ignore[reportOptionalIterable]
        # two processes first-compiling identical protos can torn-read a
        # half-written *_pb2*.py; fails loud (SyntaxError), retry heals -
        # stage protoc output + os.replace if it ever bites
        self.proto_path.mkdir(exist_ok=True)

        for stem, content in self.raw_protos.items():  # type: ignore[reportOptionalMemberAccess]
            (self.proto_path / f"{stem}.proto").write_text(content)

        if self.service_path is None:
            self.service_path = self.proto_path

    @classmethod
    def config_proto_files(
        cls,
        raw_protos: dict[str, str] | None = None,
        proto_path: str | Path | None = None,
        proto_files: list[str] | None = None,
        service_path: str | Path | None = None,
    ) -> "ProtoConfig":
        """Build the compile config.

        Args:
            raw_protos: Inline proto content (stem -> content), or ``None`` for
                file mode.
            proto_path: Directory containing ``.proto`` files.
            proto_files: Proto files to compile, or ``None`` to discover all.
            service_path: Output directory for generated modules.

        Returns:
            The resolved :class:`ProtoConfig`.

        Raises:
            ValueError: If ``raw_protos`` is passed together with
                ``proto_path`` or ``proto_files``.
        """
        if raw_protos and (proto_path or proto_files):
            raise ValueError(
                "pass either raw_protos (inline proto content) or "
                f"proto_path/proto_files (files on disk), not both; got "
                f"{sorted(raw_protos)} with proto_path={proto_path!r}, "
                f"proto_files={proto_files!r}"
            )

        return ProtoConfig(
            raw_protos=raw_protos,
            proto_path=proto_path,
            proto_files=proto_files,
            service_path=service_path,
        )

    def to_compiler_args(self) -> list[str]:
        """Build the protoc argv, including the well-known-types include path.

        Returns:
            Argument list for ``protoc.main``.
        """
        return [
            "",
            f"--proto_path={self.proto_path}",
            # well-known types (struct.proto, timestamp.proto, ...) ship inside grpc_tools
            f"-I{files('grpc_tools') / '_proto'}",
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
                "require_client_auth=True requires root_certificates "
                "(the CA bundle verifying client certs)."
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
