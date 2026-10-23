"""Shared schema: server and client pass the same content, hashing to the same dir."""

GREETER_PROTO = """
syntax = "proto3";

service Greeter {
  rpc SayHello (HelloRequest) returns (HelloReply);
}

message HelloRequest {
  string name = 1;
}

message HelloReply {
  string message = 1;
}
"""
