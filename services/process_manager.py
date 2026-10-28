"""
Spawn and stop cache-node OS processes on behalf of one parent
process - the router, when it scales the cluster from the web UI,
or the run.py launcher:

    process_manager.start(port, capacity)  -> run main.py --port ...
    process_manager.stop(port)             -> terminate a child we
                                              own, else kill via the
                                              PID file main.py wrote
    process_manager.stop_all()             -> launcher/router shutdown
                                              hook (run.py runs the
                                              router in-process, so its
                                              nodes die with it)

Nothing here knows about the cluster: it only owns processes.  The
router's routes_manage.py does the membership bookkeeping.

Logs land in data/logs/node-<port>.log, PID files in
data/pids/node-<port>.pid (written by main.py), so a node started by
another process - by hand, by a second router - can still be found and
stopped from this side.  The `spawner` hook is injectable for tests.
"""

import os
import signal
import subprocess
import sys
import threading
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def project_python() -> str:
    """Prefer the project venv so every process runs the right deps."""

    candidates = [
        os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
        os.path.join(ROOT, ".venv", "bin", "python"),
    ]

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    return sys.executable


def _default_spawner(command, cwd, log_path):
    """subprocess.Popen with output captured into `log_path`."""

    handle = open(log_path, "ab")

    kwargs = {
        "cwd": cwd,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
    }

    if os.name == "nt":
        # No flashing console windows: this runs inside the router.
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True

    return subprocess.Popen(command, **kwargs)


def _kill_pid(pid: int) -> bool:
    """Best-effort kill of a PID we do not own (hung node fallback)."""

    try:

        if os.name == "nt":
            # Restrict to python images: a stale PID file must never
            # take down an unrelated process that inherited the number.
            result = subprocess.run(
                [
                    "taskkill", "/PID", str(pid), "/T", "/F",
                    "/FI", "IMAGENAME eq python*",
                ],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0

        os.kill(pid, signal.SIGTERM)
        return True

    except (OSError, subprocess.SubprocessError):
        return False


class ProcessManager:
    """Per-process registry of the child nodes THIS process started."""

    def __init__(self, root=None, spawner=None, log_dir=None, pid_dir=None):

        self.root = root or ROOT
        self._spawner = spawner or _default_spawner

        self.log_dir = log_dir or os.path.join(self.root, "data", "logs")
        self.pid_dir = pid_dir or os.path.join(self.root, "data", "pids")

        # port -> {"process", "command", "log"}
        self._children = {}
        self._lock = threading.Lock()

    # --------------------------------------------------
    # COMMANDS + FILES
    # --------------------------------------------------

    def command(self, port: int, capacity: int) -> list:
        return [
            project_python(),
            os.path.join(self.root, "main.py"),
            "--port", str(port),
            "--capacity", str(capacity),
        ]

    def _pid_path(self, port: int) -> str:
        return os.path.join(self.pid_dir, f"node-{port}.pid")

    def _read_pid(self, port: int):
        try:
            with open(self._pid_path(port), encoding="utf-8") as handle:
                return int(handle.read().strip() or 0) or None
        except (OSError, ValueError):
            return None

    # --------------------------------------------------
    # START
    # --------------------------------------------------

    def start(self, port: int, capacity: int = 100) -> dict:
        """
        Start main.py on `port` unless one of our children already
        runs there. Returns {"started": ...} plus pid/log/command.
        """

        with self._lock:

            record = self._children.get(port)

            if record is not None and record["process"].poll() is None:
                return {
                    "port": port,
                    "started": False,
                    "already_running": True,
                    "pid": getattr(record["process"], "pid", None),
                    "log": record["log"],
                }

            os.makedirs(self.log_dir, exist_ok=True)

            log_path = os.path.join(self.log_dir, f"node-{port}.log")
            command = self.command(port, capacity)

            try:
                process = self._spawner(
                    command, cwd=self.root, log_path=log_path
                )
            except OSError as error:
                return {"port": port, "started": False, "error": repr(error)}

            self._children[port] = {
                "process": process,
                "command": command,
                "log": log_path,
            }

        return {
            "port": port,
            "started": True,
            "already_running": False,
            "pid": getattr(process, "pid", None),
            "log": log_path,
            "command": command,
        }

    # --------------------------------------------------
    # STOP
    # --------------------------------------------------

    def stop(self, port: int, grace: float = 3.0, force: bool = True) -> dict:
        """
        Stop the node process on `port`.

        Our own child is terminated gracefully first.  Otherwise the
        PID file main.py wrote is used as a last resort - skipped
        when `force` is False (the caller already asked the node to
        exit through its shutdown endpoint).
        """

        with self._lock:
            record = self._children.pop(port, None)

        if record is not None:

            process = record["process"]

            if process.poll() is None:

                try:
                    process.terminate()
                except OSError:
                    pass

                deadline = time.time() + grace

                try:
                    process.wait(timeout=max(0.1, deadline - time.time()))
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        process.kill()
                    except OSError:
                        pass

                return {"port": port, "stopped": True, "method": "child"}

            return {"port": port, "stopped": True, "method": "already-exited"}

        if not force:
            return {"port": port, "stopped": False, "reason": "not-ours"}

        pid = self._read_pid(port)

        if pid and _kill_pid(pid):
            return {"port": port, "stopped": True, "method": "pid"}

        return {"port": port, "stopped": False, "reason": "not-running"}

    def stop_all(self) -> int:
        """Stop every child of this process (router shutdown hook)."""

        with self._lock:
            ports = list(self._children)

        stopped = 0

        for port in ports:
            if self.stop(port).get("stopped"):
                stopped += 1

        return stopped

    # --------------------------------------------------
    # INSPECTION
    # --------------------------------------------------

    def is_running(self, port: int) -> bool:
        """True while one of OUR children on `port` is alive."""

        with self._lock:
            record = self._children.get(port)

        return record is not None and record["process"].poll() is None

    def pid(self, port: int):
        """Our child's PID, else the one from the PID file (may be stale)."""

        with self._lock:
            record = self._children.get(port)

        if record is not None and record["process"].poll() is None:
            return getattr(record["process"], "pid", None)

        return self._read_pid(port)

    def status(self) -> list:
        """Snapshot: one entry per port we have a record of."""

        with self._lock:
            items = [
                (port, record["process"])
                for port, record in self._children.items()
            ]

        return sorted(
            (
                {
                    "port": port,
                    "running": process.poll() is None,
                    "pid": getattr(process, "pid", None),
                }
                for port, process in items
            ),
            key=lambda entry: entry["port"]
        )


# One registry per parent process (router / run.py launcher).
process_manager = ProcessManager()
