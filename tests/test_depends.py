import dataclasses
import typing

import grpc
import pytest

from simple_grpc import Depends, SimpleGRPC


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


def make_app(tmp_path, dependencies=None):
    """Build a SimpleGRPC app over the demo proto with optional app deps.

    Args:
        tmp_path: Pytest temp directory for the protos and services.
        dependencies: Optional app-wide ``Depends`` list.

    Returns:
        The configured :class:`SimpleGRPC`.
    """
    proto_dir = tmp_path / "protos"
    proto_dir.mkdir(exist_ok=True)
    (proto_dir / "demo.proto").write_text(PROTO)
    (tmp_path / "services").mkdir(exist_ok=True)  # service_path must exist (see _compile)
    return SimpleGRPC(
        proto_path=proto_dir,
        service_path=tmp_path / "services",
        dependencies=dependencies,
    )


@pytest.fixture
def app(tmp_path):
    """App without app-level dependencies for most tests."""
    return make_app(tmp_path)


def compile_build(app):
    """Compile protos, load modules, and build the Echo servicer.

    Args:
        app: The application to compile.

    Returns:
        A ``(pb2, servicer)`` pair for the demo Echo service.
    """
    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    return pb2, app._build_servicer("Echo", pb2, pb2_grpc)


def test_sync_depend_injected(app, mocker):
    def token(context):
        return "tok-1"

    @app.rpc("Echo", "Say")
    def say(request, token=Depends(token)):
        return {"message": f"got:{token}"}

    pb2, servicer = compile_build(app)
    response = servicer.Say(pb2.EchoRequest(message="hi"), mocker.Mock())
    assert response.message == "got:tok-1"


def test_depend_receives_request(app, mocker):
    def name_of(request):
        return request.message

    @app.rpc("Echo", "Say")
    def say(request, name=Depends(name_of)):
        return {"message": f"dep:{name}"}

    pb2, servicer = compile_build(app)
    response = servicer.Say(pb2.EchoRequest(message="hi"), mocker.Mock())
    assert response.message == "dep:hi"


def test_nested_depend(app, mocker):
    def leaf(context):
        return "leaf"

    def branch(t=Depends(leaf)):
        return f"{t}+branch"

    @app.rpc("Echo", "Say")
    def say(request, value=Depends(branch)):
        return {"message": value}

    pb2, servicer = compile_build(app)
    response = servicer.Say(pb2.EchoRequest(), mocker.Mock())
    assert response.message == "leaf+branch"


def test_generator_dep_pre_post(app, mocker):
    events = []

    def audit():
        events.append("pre")
        yield
        events.append("post")

    @app.rpc("Echo", "Say")
    def say(request, _audit=Depends(audit)):
        events.append("handler")
        return {"message": "x"}

    pb2, servicer = compile_build(app)
    servicer.Say(pb2.EchoRequest(), mocker.Mock())
    assert events == ["pre", "handler", "post"]


def test_generator_teardown_on_handler_error(app, mocker):
    events = []

    def audit():
        events.append("pre")
        yield
        events.append("post")

    @app.rpc("Echo", "Say")
    def say(request, _audit=Depends(audit)):
        events.append("handler")
        raise RuntimeError("boom")

    pb2, servicer = compile_build(app)
    with pytest.raises(RuntimeError, match="boom"):
        servicer.Say(pb2.EchoRequest(), mocker.Mock())
    assert events == ["pre", "handler", "post"]


def test_generate_dep_value_injected(app, mocker):
    def half(request):
        return request.message[:2]

    @app.rpc("Echo", "Say")
    def say(request, head=Depends(half)):
        return {"message": head}

    pb2, servicer = compile_build(app)
    response = servicer.Say(pb2.EchoRequest(message="abcd"), mocker.Mock())
    assert response.message == "ab"


def test_app_then_signature_dep_order(tmp_path, mocker):
    events = []

    def app_dep_1():
        events.append("app1")

    def app_dep_2():
        events.append("app2")

    def sig_dep():
        events.append("sig")

    scoped = make_app(tmp_path, dependencies=[Depends(app_dep_1), Depends(app_dep_2)])

    @scoped.rpc("Echo", "Say")
    def say(request, _dep=Depends(sig_dep)):
        events.append("handler")
        return {"message": "x"}

    pb2, servicer = compile_build(scoped)
    servicer.Say(pb2.EchoRequest(), mocker.Mock())
    assert events == ["app1", "app2", "sig", "handler"]


def test_dependency_abort(app, mocker):
    class Abort(Exception):
        pass

    def forbidden(context):
        context.abort(grpc.StatusCode.PERMISSION_DENIED, "denied")

    @app.rpc("Echo", "Say")
    def say(request, ok=Depends(forbidden)):
        return {"message": "should-not-happen"}

    pb2, servicer = compile_build(app)
    context = mocker.Mock()
    context.abort = mocker.Mock(side_effect=Abort)
    with pytest.raises(Abort):
        servicer.Say(pb2.EchoRequest(), context)
    context.abort.assert_called_once_with(grpc.StatusCode.PERMISSION_DENIED, "denied")


def test_unknown_depend_param_raises(app):
    def bad(whatever):
        return "x"

    @app.rpc("Echo", "Say")
    def say(request, bad_value=Depends(bad)):
        return {"message": bad_value}

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    with pytest.raises(ValueError, match=r"dependency 'bad' declares unregistrable"):
        app._build_servicer("Echo", pb2, pb2_grpc)


def test_unknown_nested_depend_param_raises(app):
    def bad_inner(whatever):
        return "x"

    def front(t=Depends(bad_inner)):
        return t

    @app.rpc("Echo", "Say")
    def say(request, value=Depends(front)):
        return {"message": value}

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    with pytest.raises(ValueError, match=r"dependency 'bad_inner' declares unregistrable"):
        app._build_servicer("Echo", pb2, pb2_grpc)


def test_unknown_handler_param_still_raises(app):
    @app.rpc("Echo", "Say")
    def say(req):
        return {}

    app._compile()
    pb2, pb2_grpc = app._load_generated()["demo"]
    with pytest.raises(ValueError, match="declares unregistrable parameter"):
        app._build_servicer("Echo", pb2, pb2_grpc)


def test_annotated_depend(app, mocker):
    """Share one dependency across handlers via an Annotated alias."""

    def common(request):
        return f"common:{request.message}"

    CommonsDep = typing.Annotated[str, Depends(common)]

    @app.rpc("Echo", "Say")
    def say(request, token: CommonsDep):
        return {"message": token}

    pb2, servicer = compile_build(app)
    response = servicer.Say(pb2.EchoRequest(message="hi"), mocker.Mock())
    assert response.message == "common:hi"


def test_annotated_depend_shared_and_nested(app, mocker):
    def get_user(context):
        return "u1"

    UserDep = typing.Annotated[str, Depends(get_user)]

    def greeting(user: UserDep, request):  # nested: greeting itself declares UserDep
        return f"hi-{user} {request.message}"

    GreetingDep = typing.Annotated[str, Depends(greeting)]  # nested dep sees UserDep

    @app.rpc("Echo", "Say")
    def say(request, msg: GreetingDep, user: UserDep):
        return {"message": msg + ":" + user}

    pb2, servicer = compile_build(app)
    response = servicer.Say(pb2.EchoRequest(message="hm"), mocker.Mock())
    assert response.message == "hi-u1 hm:u1"


def test_depends_is_frozen():
    def dep():
        return None

    marker = Depends(dep)
    assert marker.dependency is dep
    with pytest.raises(dataclasses.FrozenInstanceError, match="cannot assign"):
        marker.dependency = dep  # type: ignore[misc]  # frozen dataclass
