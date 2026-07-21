"""Deploy-hang guard (2026-07-21 outage): the hosted console's stop signal
must exit the process within a bounded grace even when a worker thread is
wedged in C code — otherwise a machine swap serves nothing for the whole
fly.toml kill_timeout."""
import os
import threading


def test_exit_backstop_arms_a_daemon_hard_exit_and_chains(monkeypatch):
    import uvicorn
    from mm import cli

    calls = {}

    class FakeServer:
        def handle_exit(self, sig, frame):
            calls["orig"] = (sig, frame)

    original = FakeServer.handle_exit
    monkeypatch.setattr(uvicorn, "Server", FakeServer)

    timers = []

    class FakeTimer:
        def __init__(self, interval, fn, args=()):
            self.interval, self.fn, self.args = interval, fn, args
            self.daemon = False
            self.started = False
            timers.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(threading, "Timer", FakeTimer)

    cli._install_exit_backstop(grace=20.0)
    assert uvicorn.Server.handle_exit is not original

    uvicorn.Server.handle_exit(FakeServer(), 15, None)
    assert calls["orig"] == (15, None)          # graceful path still runs
    (t,) = timers
    assert t.started and t.daemon               # armed, never blocks exit
    assert t.interval == 20.0
    assert t.fn is os._exit and t.args == (0,)  # hard exit, clean code
