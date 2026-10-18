"""Unit tests for ``SimpleGRPC._stream`` (per-item reply, abort mapping, teardown)."""

import pytest

from simple_grpc import SimpleGRPC
from simple_grpc.exceptions import NotFoundError


def test_stream_serializes_each_item(mocker):
    """Every item from the handler iterator is passed through ``utils.reply``."""
    items = iter([mocker.sentinel.one, mocker.sentinel.two])
    reply = mocker.patch("simple_grpc.utils.reply", side_effect=lambda v, _: v)

    out = list(SimpleGRPC._stream(items, mocker.sentinel.cls, mocker.Mock(), []))

    assert out == [mocker.sentinel.one, mocker.sentinel.two]
    assert [c.args[0] for c in reply.call_args_list] == [mocker.sentinel.one, mocker.sentinel.two]
    assert all(c.args[1] is mocker.sentinel.cls for c in reply.call_args_list)


def test_stream_aborts_on_grpc_exception(mocker):
    """A GRPCException mid-stream maps to ``context.abort``; later items are dropped."""
    mocker.patch("simple_grpc.utils.reply", side_effect=lambda v, _: v)
    context = mocker.Mock()
    context.abort.side_effect = RuntimeError("abort raises")

    def gen():
        yield "first"
        raise NotFoundError("gone")
        yield "never"

    tails = []
    with pytest.raises(RuntimeError):
        list(SimpleGRPC._stream(gen(), mocker.Mock(), context, tails))

    context.abort.assert_called_once_with(NotFoundError.code, "gone")


def test_stream_runs_tails_after_last_item(mocker):
    """Teardown tails run exactly once, after the stream is consumed."""
    mocker.patch("simple_grpc.utils.reply", side_effect=lambda v, _: v)
    ran = []

    def gen():
        yield "item"

    tails = [lambda: ran.append("t1"), lambda: ran.append("t2")]
    out = list(SimpleGRPC._stream(gen(), mocker.Mock(), mocker.Mock(), tails))

    assert out == ["item"]
    assert ran == ["t1", "t2"]
