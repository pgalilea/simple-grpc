"""Tests for the hot-reload supervisor (simple_grpc.reloading)."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from watchfiles import Change

from simple_grpc import SimpleGRPC, reloading


# Module-level app: dev() discovers it in this module's globals via frame inspection.
app = SimpleGRPC(proto_path="protos", service_path="grpc_services")


def _filter() -> reloading.SourceFilter:
    """Build the filter with its default excludes only."""
    return reloading.SourceFilter()


def test_filter_accepts_user_sources(tmp_path: Path) -> None:
    assert _filter()(Change.modified, str(tmp_path / "server.py"))
    assert _filter()(Change.modified, str(tmp_path / "route.proto"))


def test_filter_rejects_generated_modules(tmp_path: Path) -> None:
    assert not _filter()(Change.modified, str(tmp_path / "route_pb2.py"))
    assert not _filter()(Change.modified, str(tmp_path / "route_pb2_grpc.py"))


def test_filter_rejects_junk(tmp_path: Path) -> None:
    assert not _filter()(Change.modified, str(tmp_path / "server.pyc"))
    assert not _filter()(Change.modified, str(tmp_path / ".venv" / "server.py"))
    assert not _filter()(Change.modified, str(tmp_path / "notes.txt"))


def test_filter_rejects_excluded_dirs_and_globs(tmp_path: Path) -> None:
    protos = tmp_path / "protos"
    flt = reloading.SourceFilter([protos, tmp_path / "gen", "secret*.py"])
    assert not flt(Change.modified, str(protos / "route.proto"))
    assert not flt(Change.modified, str(tmp_path / "gen" / "nested" / "x.py"))
    assert not flt(Change.modified, str(tmp_path / "secret_keys.py"))


def test_locate_app_returns_importable_module() -> None:
    frame = SimpleNamespace(f_globals={"app": app, "__spec__": SimpleNamespace(name="pkg.mod")})
    assert reloading.locate_app(app, frame) == ("pkg.mod", "app")


def test_locate_app_returns_script_path(tmp_path: Path) -> None:
    other = SimpleGRPC(proto_path=tmp_path / "protos", service_path=tmp_path / "services")
    script = tmp_path / "server.py"
    frame = SimpleNamespace(f_globals={"other": other, "__spec__": None, "__file__": str(script)})
    assert reloading.locate_app(other, frame) == (str(script.resolve()), "other")


def test_locate_app_rejects_unbound_app() -> None:
    other = SimpleGRPC(proto_path="protos", service_path="grpc_services")
    frame = SimpleNamespace(f_globals={"__spec__": None})
    with pytest.raises(RuntimeError, match="module-level"):
        reloading.locate_app(other, frame)


def test_serve_in_child_runs_script_app(mocker, tmp_path: Path) -> None:
    protos = tmp_path / "protos"
    protos.mkdir()
    (protos / "p.proto").write_text('syntax = "proto3";')
    services = tmp_path / "services"
    services.mkdir()
    script = tmp_path / "server.py"
    script.write_text(
        "from simple_grpc import SimpleGRPC\n"
        f"app = SimpleGRPC(proto_path={str(protos)!r}, service_path={str(services)!r})\n"
    )
    start = mocker.patch.object(SimpleGRPC, "_start")

    reloading.serve_in_child(str(script), "app")

    start.assert_called_once()


def test_serve_with_reload_wires_run_process(mocker, tmp_path: Path) -> None:
    other = SimpleGRPC(proto_path=tmp_path / "protos", service_path=tmp_path / "services")
    run_process = mocker.patch("simple_grpc.reloading.run_process")

    result = reloading.serve_with_reload(other, "pkg.mod", "app")

    assert result is run_process.return_value
    run_process.assert_called_once()
    args, kwargs = run_process.call_args
    assert args == (Path.cwd(),)
    assert kwargs["target"] is reloading.serve_in_child
    assert kwargs["args"] == ("pkg.mod", "app")
    flt = kwargs["watch_filter"]
    assert isinstance(flt, reloading.SourceFilter)
    assert other.proto_config.proto_path in flt.excludes
    assert other.proto_config.service_path in flt.excludes


def test_serve_with_reload_passes_dirs_and_excludes(mocker, tmp_path: Path) -> None:
    other = SimpleGRPC(proto_path=tmp_path / "protos", service_path=tmp_path / "services")
    run_process = mocker.patch("simple_grpc.reloading.run_process")

    reloading.serve_with_reload(
        other,
        "server.py",
        "app",
        reload_dirs=["src", tmp_path],
        reload_excludes=["skip*.py"],
    )

    args, kwargs = run_process.call_args
    assert args == ("src", tmp_path)
    assert "skip*.py" in kwargs["watch_filter"].excludes


def test_dev_without_reload_starts_in_process(mocker) -> None:
    start = mocker.patch.object(app, "_start")

    app.dev(reload=False)

    start.assert_called_once()


def test_dev_hot_reloads_via_supervisor(mocker) -> None:
    serve = mocker.patch("simple_grpc.reloading.serve_with_reload")

    app.dev()

    serve.assert_called_once()
    args, kwargs = serve.call_args
    assert args[0] is app
    assert "test_reloading" in args[1]
    assert args[2] == "app"
    assert kwargs == {"reload_dirs": None, "reload_excludes": None}


def test_dev_rejects_non_module_level_app() -> None:
    other = SimpleGRPC(proto_path="protos", service_path="grpc_services")

    with pytest.raises(RuntimeError, match="module-level"):
        other.dev()
