"""Shared schema: server and client pass the same content, hashing to the same dir."""

CALC_PROTO = """
syntax = "proto3";

service Calculator {
  rpc Add (AddRequest) returns (AddReply);
}

message AddRequest {
  int64 a = 1;
  int64 b = 2;
}

message AddReply {
  int64 sum = 1;
}
"""
