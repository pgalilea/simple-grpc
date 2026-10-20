"""Dependency-injection example: fake auth, fake SQLAlchemy-like DB, logging.

Three dependencies resolve around each RPC handler:

- ``auth_dep``: validates the ``authorization`` bearer header, aborting with
  ``UNAUTHENTICATED`` when it is missing or malformed; ``current_user`` builds
  on it, showing nested dependency resolution.
- ``db_session``: yields a fake ``Session`` mimicking SQLAlchemy's
  ``session.get(Model, pk)``, opening/closing around the handler.
- ``log_dep``: logs the request start and completion.

``UserDep`` and ``DbSession`` show the ``Annotated`` alias style so handlers
stay clean; ``log_dep`` uses the plain ``Depends(...)`` default style.
"""

from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Annotated

from simple_grpc import Depends, SimpleGRPC, status


TMP_DIR = Path(tempfile.gettempdir()) / "simple_grpc_example_3"
TMP_DIR.mkdir(exist_ok=True)
(TMP_DIR / "notes.proto").write_text("""
syntax = "proto3";

service Notes {
  rpc GetNote (GetNoteRequest) returns (NoteReply);
  rpc WhoAmI (WhoAmIRequest) returns (WhoAmIReply);
}

message GetNoteRequest {
  int64 id = 1;
}

message NoteReply {
  int64 id = 1;
  string title = 2;
}

message WhoAmIRequest {
}

message WhoAmIReply {
  string user = 1;
}
""")

app = SimpleGRPC(
    proto_path=TMP_DIR,
    proto_files=["notes.proto"],
    service_path=TMP_DIR,
    port=50054,
)

VALID_TOKENS = {"abc123": "alice", "def456": "bob"}


# --- Dependency 1: bearer token in the authorization header -----------------


def auth_dep(context) -> str:
    """Validate the ``authorization: Bearer <token>`` metadata header."""
    metadata = dict(context.invocation_metadata())
    auth = metadata.get("authorization", "")
    scheme, _, token = auth.partition(" ")

    if scheme.lower() != "bearer" or token not in VALID_TOKENS:
        context.abort(status.UNAUTHENTICATED, "invalid bearer token")

    return token


def current_user(token: Annotated[str, Depends(auth_dep)]) -> str:
    """Resolve the token to a user, on top of ``auth_dep`` (nested dep)."""
    return VALID_TOKENS[token]


UserDep = Annotated[str, Depends(current_user)]


# --- Dependency 2: fake database, SQLAlchemy-flavoured ----------------------


@dataclass
class Note:
    """Fake ORM model row."""

    id: int
    title: str


class Session:
    """Fake ``sqlalchemy.orm.Session``: ``session.get(Model, pk)`` lookups."""

    def __init__(self) -> None:
        self._notes = {n.id: n for n in [Note(1, "shopping"), Note(2, "taxes")]}

    def get(self, model: type, pk: int) -> Note | None:
        """Return the row for ``pk`` (like ``Session.get``) or ``None``."""
        return self._notes.get(pk) if model is Note else None


def db_session():
    """Open a fake session for the handler and close it afterwards."""
    print("db: session opened")
    yield Session()
    print("db: session closed")


DbSession = Annotated[Session, Depends(db_session)]


# --- Dependency 3: logging ---------------------------------------------------


def log_dep(request, context):
    """Log the RPC start and completion around the handler."""
    print(f"log: -> {type(request).__name__}")
    yield
    print(f"log: <- {type(request).__name__}")


# --- Handlers ----------------------------------------------------------------


@app.rpc("Notes", "GetNote")
def get_note(request, context, user: UserDep, db: DbSession, _log=Depends(log_dep)):
    """Fetch a note through the injected fake session."""
    print(f"log: {user} requested note {request.id}")
    note = db.get(Note, request.id)
    if note is None:
        context.abort(status.NOT_FOUND, f"note {request.id} not found")
    return {"id": note.id, "title": note.title}  # type: ignore


@app.rpc("Notes", "WhoAmI")
def who_am_i(request, user: UserDep, _log=Depends(log_dep)):
    """Report which user the bearer token resolved to."""
    return {"user": user}


if __name__ == "__main__":
    app.dev()  # hot reload: edit this file and the server restarts
