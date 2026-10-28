"""
ProcessManager - the spawn/stop machinery behind UI node scaling.

Everything here runs against a FAKE spawner: no real child process is
ever created, so these tests are instant and cannot stray onto live
ports.  The one exception is project_python(), which only resolves a
path.
"""

import os

from services.process_manager import ProcessManager, project_python


class FakeProcess:
    """Stands in for subprocess.Popen (enough of the surface)."""

    _next_pid = 41000

    def __init__(self, command):
        FakeProcess._next_pid += 1

        self.pid = FakeProcess._next_pid
        self.command = command
        self.terminated = False
        self.killed = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode


class FakeSpawner:
    """Injectable spawner: records commands, never touches the OS."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.processes = {}

    def __call__(self, command, cwd=None, log_path=None):

        if self.fail:
            raise OSError("spawn refused")

        self.calls.append({
            "command": command,
            "cwd": cwd,
            "log_path": log_path,
        })

        process = FakeProcess(command)
        self.processes[command[command.index("--port") + 1]] = process

        return process


def make_manager(tmp_path, spawner=None):
    spawner = spawner or FakeSpawner()

    manager = ProcessManager(
        root=str(tmp_path),
        spawner=spawner,
        log_dir=str(tmp_path / "logs"),
        pid_dir=str(tmp_path / "pids")
    )

    return manager, spawner


# =========================================================
# START
# =========================================================

def test_start_spawns_main_py_with_port_and_capacity(tmp_path):

    manager, spawner = make_manager(tmp_path)

    result = manager.start(9500, capacity=123)

    assert result["started"] is True
    assert result["already_running"] is False
    assert result["pid"] is not None

    command = spawner.calls[0]["command"]

    assert command[1].endswith("main.py")
    assert command[command.index("--port") + 1] == "9500"
    assert command[command.index("--capacity") + 1] == "123"

    # Output captured per node, working dir is the project root.
    assert spawner.calls[0]["log_path"].endswith("node-9500.log")
    assert spawner.calls[0]["cwd"] == str(tmp_path)

    assert manager.is_running(9500) is True
    assert manager.pid(9500) == result["pid"]


def test_start_twice_spawns_only_one_process(tmp_path):

    manager, spawner = make_manager(tmp_path)

    first = manager.start(9500)
    second = manager.start(9500)

    assert first["started"] is True
    assert second["started"] is False
    assert second["already_running"] is True
    assert len(spawner.calls) == 1


def test_spawn_failure_is_reported_not_raised(tmp_path):

    manager, spawner = make_manager(tmp_path, spawner=FakeSpawner(fail=True))

    result = manager.start(9500)

    assert result["started"] is False
    assert "error" in result
    assert manager.is_running(9500) is False


def test_dead_child_gets_respawned_on_next_start(tmp_path):

    manager, spawner = make_manager(tmp_path)

    manager.start(9500)

    # The child exited on its own (crash)...
    process = spawner.processes["9500"]
    process.returncode = 1

    assert manager.is_running(9500) is False

    # ...so starting again must spawn a fresh one.
    result = manager.start(9500)

    assert result["started"] is True
    assert len(spawner.calls) == 2


# =========================================================
# STOP
# =========================================================

def test_stop_terminates_our_own_child(tmp_path):

    manager, spawner = make_manager(tmp_path)

    manager.start(9500)
    process = spawner.processes["9500"]

    result = manager.stop(9500)

    assert result["stopped"] is True
    assert result["method"] == "child"
    assert process.terminated is True
    assert manager.is_running(9500) is False


def test_stop_without_force_only_touches_our_children(tmp_path):

    manager, _ = make_manager(tmp_path)

    # force=False: the caller already asked a (possibly foreign)
    # node to exit via /internal/shutdown - we must NOT escalate to
    # a PID-file kill when we do not own the port.
    result = manager.stop(9500, force=False)

    assert result == {
        "port": 9500, "stopped": False, "reason": "not-ours"
    }


def test_stop_unknown_port_reports_not_running(tmp_path):

    manager, _ = make_manager(tmp_path)

    result = manager.stop(9500)

    assert result["stopped"] is False
    assert result["reason"] == "not-running"


def test_stop_all_stops_every_child(tmp_path):

    manager, spawner = make_manager(tmp_path)

    manager.start(9500)
    manager.start(9501)
    manager.start(9502)

    assert manager.stop_all() == 3
    assert manager.is_running(9500) is False
    assert manager.is_running(9501) is False
    assert manager.is_running(9502) is False
    assert all(p.terminated for p in spawner.processes.values())


def test_stop_all_is_safe_on_an_empty_manager(tmp_path):

    manager, _ = make_manager(tmp_path)

    assert manager.stop_all() == 0


# =========================================================
# PID FILES (cross-process stop) + HELPERS
# =========================================================

def test_pid_falls_back_to_the_pid_file(tmp_path):

    manager, spawner = make_manager(tmp_path)

    # main.py writes data/pids/node-<port>.pid; a node started by
    # ANOTHER process (not our child) is still discoverable.
    os.makedirs(str(tmp_path / "pids"), exist_ok=True)

    with open(tmp_path / "pids" / "node-9600.pid", "w") as handle:
        handle.write("12345")

    assert manager.pid(9600) == 12345
    assert manager.is_running(9600) is False  # not OUR child
    assert len(spawner.calls) == 0


def test_status_lists_ports_with_liveness(tmp_path):

    manager, _ = make_manager(tmp_path)

    manager.start(9500)
    manager.start(9501)

    status = manager.status()

    assert [entry["port"] for entry in status] == [9500, 9501]
    assert all(entry["running"] for entry in status)


def test_project_python_prefers_the_project_venv():

    python = project_python()

    assert python.endswith("python.exe") or python.endswith("python")
    assert os.path.exists(python)
