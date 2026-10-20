"""Client for dev_server.py: valid and invalid bearer tokens, generated notes."""

from pathlib import Path
import sys
import tempfile

import grpc


sp = Path(tempfile.gettempdir()) / "simple_grpc_example_3"
sys.path.insert(0, str(sp))

import notes_pb2 as pb2  # type: ignore # noqa: E402
from notes_pb2_grpc import NotesStub  # type: ignore # noqa: E402


def metadata(token: str) -> list[tuple[str, str]]:
    """Build the authorization metadata pair for one bearer token."""
    return [("authorization", f"Bearer {token}")]


with grpc.insecure_channel("localhost:50054") as channel:
    notes = NotesStub(channel)

    # Valid token: auth dep resolves it to a user.
    reply = notes.WhoAmI(pb2.WhoAmIRequest(), metadata=metadata("abc123"))
    assert reply.user == "alice", reply.user

    # DB session dep: note fetched through session.get, log dep around it.
    note = notes.GetNote(pb2.GetNoteRequest(id=2), metadata=metadata("abc123"))
    assert note.title == "taxes", note.title

    # Unknown note aborts with NOT_FOUND.
    try:
        notes.GetNote(pb2.GetNoteRequest(id=99), metadata=metadata("abc123"))
        # raise SystemExit("expected NOT_FOUND, got other thing")
    except grpc.RpcError as err:
        print(err)
        assert err.code() == grpc.StatusCode.NOT_FOUND, err.code()

    # Invalid token aborts with UNAUTHENTICATED from the auth dependency.
    try:
        notes.WhoAmI(pb2.WhoAmIRequest(), metadata=metadata("nope"))
        # raise SystemExit("expected UNAUTHENTICATED, got other thing")
    except grpc.RpcError as err:
        print(err)
        assert err.code() == grpc.StatusCode.UNAUTHENTICATED, err.code()

    # Missing header: same abort path.
    try:
        notes.WhoAmI(pb2.WhoAmIRequest())
        # raise SystemExit("expected UNAUTHENTICATED, got other thing")
    except grpc.RpcError as err:
        print(err)
        assert err.code() == grpc.StatusCode.UNAUTHENTICATED, err.code()

print("\nall RPCs OK")
