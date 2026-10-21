"""Shared schema: server and client pass the same content, hashing to the same dir."""

NOTES_PROTO = """
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
"""
