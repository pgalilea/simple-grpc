"""Hot reload for :meth:`SimpleGRPC.dev` — serve in a child process, restart on changes."""

from collections.abc import Sequence
from fnmatch import fnmatch
import importlib
import logging
from pathlib import Path
import runpy
from types import FrameType
from typing import TYPE_CHECKING

from watchfiles import Change, DefaultFilter, run_process


if TYPE_CHECKING:
    from simple_grpc.applications import SimpleGRPC


logger = logging.getLogger("simple_grpc.error")


class SourceFilter(DefaultFilter):
    """Reload trigger filter: user sources pass, protoc output and excludes don't."""

    SUFFIXES = (".py", ".pyi", ".proto")
    DEFAULT_EXCLUDES = ("*_pb2.py", "*_pb2_grpc.py")

    def __init__(self, excludes: Sequence[str | Path] = ()) -> None:
        """
        Store the excludes; ``DefaultFilter`` junk dirs stay ignored.

        Args:
            excludes: Glob patterns (matched against file name and path)
                and/or directories (anything under them is ignored).
        """
        super().__init__()
        self.excludes = (*self.DEFAULT_EXCLUDES, *excludes)

    def __call__(self, change: Change, path: str) -> bool:
        """
        Decide whether one changed path should trigger a reload.

        Args:
            change: The kind of change watchfiles reports.
            path: The changed path, as reported by the watcher.

        Returns:
            True if the change should restart the server.
        """
        if not path.endswith(self.SUFFIXES):
            return False
        resolved = Path(path).resolve()
        if self._excluded(resolved):
            return False
        return super().__call__(change, str(resolved))

    def _excluded(self, path: Path) -> bool:
        """
        Match ``path`` against the excludes.

        Args:
            path: Resolved path of the changed file.

        Returns:
            True if ``path`` matches any exclude glob or sits under an
            excluded directory.
        """
        for exclude in self.excludes:
            if fnmatch(path.name, str(exclude)) or fnmatch(str(path), str(exclude)):
                return True
            exclude_dir = Path(exclude).resolve()
            if path == exclude_dir or exclude_dir in path.parents:
                return True
        return False


def locate_app(app: "SimpleGRPC", frame: FrameType) -> tuple[str, str]:
    """
    Find how a freshly spawned child process can rebuild ``app``.

    Args:
        app: The app whose ``dev()`` was called.
        frame: The caller's frame (the user's module).

    Returns:
        ``(module_ref, attr)``: the importable module name when the user ran
        ``python -m pkg.mod``, or the script file path when the user ran
        ``python script.py``.

    Raises:
        RuntimeError: If the app is not bound to a module-level variable, or
            not defined in a module or script file at all.
    """
    attr = next((k for k, v in frame.f_globals.items() if v is app), None)
    if attr is None:
        raise RuntimeError(
            "hot reload needs the app bound to a module-level variable, "
            "e.g. `app = SimpleGRPC(...)` at module level"
        )
    spec = frame.f_globals.get("__spec__")
    if spec is not None:
        return spec.name, attr
    script = frame.f_globals.get("__file__")
    if script is None:
        raise RuntimeError("hot reload needs the app defined in a module or script file")
    return str(Path(script).resolve()), attr


def serve_in_child(module_ref: str, attr: str) -> None:
    """
    Run in the spawned child: rebuild the app from source and serve it.

    Args:
        module_ref: Importable module name (``pkg.mod``), or a ``.py``
            script path.
        attr: Name of the app variable in that module.
    """
    if module_ref.endswith(".py"):
        namespace: dict[str, SimpleGRPC] = runpy.run_path(module_ref, run_name="simple_grpc_dev")
        child_app = namespace[attr]
    else:
        child_app = getattr(importlib.import_module(module_ref), attr)
    try:
        child_app._start()  # type: ignore[reportPrivateUsage]
    except KeyboardInterrupt:
        # SIGINT from the reloader: exit quietly, the parent respawns us.
        pass


def serve_with_reload(
    app: "SimpleGRPC",
    module_ref: str,
    attr: str,
    reload_dirs: Sequence[str | Path] | None = None,
    reload_excludes: Sequence[str | Path] | None = None,
) -> int:
    """
    Serve the app in a child process, restarting it on source changes.

    Args:
        app: App to serve; its proto and service directories are excluded
            from watching (protoc rewrites them at every child start, which
            would otherwise loop reloads forever).
        module_ref: Module name or script path the child rebuilds the app from.
        attr: App variable name in that module.
        reload_dirs: Directories to watch; defaults to the working directory.
        reload_excludes: Extra glob patterns and/or directories to ignore,
            on top of the defaults (proto/service directories,
            ``*_pb2.py``/``*_pb2_grpc.py``).

    Returns:
        The number of reloads performed (see ``watchfiles.run_process``).
    """

    proto = app.proto_config
    logger.info(
        "Hot reload enabled, watching directories: %s",
        (reload_dirs or [str(Path.cwd())]),
    )
    return run_process(
        *(reload_dirs or (Path.cwd(),)),
        target=serve_in_child,
        args=(module_ref, attr),
        watch_filter=SourceFilter([proto.proto_path, proto.service_path, *(reload_excludes or ())]),
        callback=_log_changes,
    )


def _log_changes(changes: set[tuple[Change, str]]) -> None:
    """
    Log which files triggered a reload.

    Args:
        changes: The changes watchfiles reported for this reload.
    """
    logger.info("Reloading due to changes in: %s", ", ".join(str(path) for _, path in changes))
