import grpc
import pytest

from simple_grpc import SimpleGRPC
from simple_grpc.models import ServerCredentials


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

PEM_KEY = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"
PEM_CERT = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n"


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


def test_server_credentials_resolves_paths(tmp_path):
    key = tmp_path / "server.key"
    key.write_text(PEM_KEY)
    cert = tmp_path / "server.pem"
    cert.write_text(PEM_CERT)
    sc = ServerCredentials(private_key=key, certificate_chain=cert)
    assert sc.private_key == PEM_KEY.encode()
    assert sc.certificate_chain == PEM_CERT.encode()
    assert sc.root_certificates is None


def test_server_credentials_accepts_content():
    sc = ServerCredentials(private_key=PEM_KEY, certificate_chain=PEM_CERT.encode())
    assert sc.private_key == PEM_KEY.encode()


def test_server_credentials_rejects_missing_file(tmp_path):
    with pytest.raises(ValueError, match="private_key"):
        ServerCredentials(private_key=tmp_path / "missing.pem", certificate_chain=PEM_CERT)


def test_server_credentials_rejects_non_pem():
    # a path passed as str (a classic mixup) must fail here, not at bind time
    with pytest.raises(ValueError, match="PEM"):
        ServerCredentials(private_key="certs/server.key", certificate_chain=PEM_CERT)


def test_server_credentials_rejects_mtls_without_roots():
    with pytest.raises(ValueError, match="root_certificates"):
        ServerCredentials(private_key=PEM_KEY, certificate_chain=PEM_CERT, require_client_auth=True)


def test_dev_with_credentials_binds_secure(app, mocker, tmp_path):
    server = mocker.Mock()
    mocker.patch.object(grpc, "server", return_value=server)
    fake_creds = mocker.Mock()
    mocker.patch.object(grpc, "ssl_server_credentials", return_value=fake_creds)
    key = tmp_path / "server.key"
    key.write_text(PEM_KEY)
    cert = tmp_path / "server.pem"
    cert.write_text(PEM_CERT)
    app.credentials = ServerCredentials(private_key=key, certificate_chain=cert)

    @app.rpc("Echo", "Say")
    def say(request, context):
        return {"message": "ok"}

    app.run()

    server.add_secure_port.assert_called_once_with("[::]:50051", fake_creds)
    server.add_insecure_port.assert_not_called()
    kwargs = grpc.ssl_server_credentials.call_args.kwargs
    assert kwargs["private_key_certificate_chain_pairs"] == [(PEM_KEY.encode(), PEM_CERT.encode())]
    assert kwargs["root_certificates"] is None
    assert kwargs["require_client_auth"] is False
