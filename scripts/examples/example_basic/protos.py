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

service Calculator {
  rpc Add (AddRequest) returns (AddReply);
  rpc CountUp (CountUpRequest) returns (stream CountUpReply);
}

message AddRequest {
  int64 a = 1;
  int64 b = 2;
}

message AddReply {
  int64 sum = 1;
}

message CountUpRequest {
  int64 up_to = 1;
}

message CountUpReply {
  int64 n = 1;
}
"""
