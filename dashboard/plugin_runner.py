import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time


PLUGIN_TIMEOUT_SECONDS = 15
MAX_PLUGIN_TIMEOUT_SECONDS = 120
PLUGIN_CPU_LIMIT_SECONDS = 10
PLUGIN_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
MAX_PLUGIN_CODE_BYTES = 64 * 1024
MAX_PLUGIN_REQUEST_BYTES = 64 * 1024
MAX_PLUGIN_OUTPUT_BYTES = 256 * 1024
MAX_PLUGIN_STDERR_BYTES = 32 * 1024


class PluginExecutionError(Exception):
    pass


class PluginExecutionTimeout(PluginExecutionError):
    pass


class PluginOutputLimitExceeded(PluginExecutionError):
    pass


class PluginWorkerBusy(PluginExecutionError):
    pass


_PLUGIN_WORKER_SLOT = threading.BoundedSemaphore(1)


_WORKER_SOURCE = r"""
import contextlib
import json
import sys

try:
    import resource
except ImportError:
    resource = None

if resource is not None:
    memory_limit = __MEMORY_LIMIT__
    resource.setrlimit(resource.RLIMIT_CPU, (__CPU_LIMIT__, __CPU_LIMIT__))
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    resource.setrlimit(resource.RLIMIT_FSIZE, (__OUTPUT_LIMIT__, __OUTPUT_LIMIT__))
    if hasattr(resource, "RLIMIT_CORE"):
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (8, 8))

job = json.loads(sys.stdin.buffer.read())
namespace = {"__name__": "__plugin__"}
try:
    with contextlib.redirect_stdout(sys.stderr):
        exec(compile(job["code"], "<plugin>", "exec"), namespace, namespace)
        handler = namespace.get("handler")
        if not callable(handler):
            raise TypeError("Plugin source must define handler(request).")
        result = handler(job["request"])
    if isinstance(result, tuple):
        if len(result) != 2:
            raise TypeError("Plugin tuple responses must contain a dictionary and status code.")
        payload, status_code = result
        if (
            not isinstance(payload, dict)
            or type(status_code) is not int
            or not 200 <= status_code <= 599
        ):
            raise TypeError(
                "Plugin tuple responses require a dictionary and HTTP status from 200 to 599."
            )
    elif isinstance(result, dict):
        payload, status_code = result, 200
    else:
        raise TypeError(
            "handler(request) must return a dictionary or (dictionary, status_code)."
        )
    sys.stdout.buffer.write(
        json.dumps(
            {"payload": payload, "status_code": status_code},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
except Exception as error:
    sys.stderr.write(type(error).__name__ + ": " + str(error)[:1000])
    sys.exit(1)
""".replace("__MEMORY_LIMIT__", str(PLUGIN_MEMORY_LIMIT_BYTES)).replace(
    "__CPU_LIMIT__", str(PLUGIN_CPU_LIMIT_SECONDS)
).replace("__OUTPUT_LIMIT__", str(MAX_PLUGIN_OUTPUT_BYTES))


def _read_capped(stream, limit, output, exceeded):
    while True:
        chunk = stream.read(8192)
        if not chunk:
            return
        if len(output) + len(chunk) > limit:
            exceeded.set()
            return
        output.extend(chunk)


def _kill_process_tree(process):
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()


def _clean_environment():
    environment = {}
    for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
        if os.name == "nt" and os.environ.get(key):
            environment[key] = os.environ[key]
    return environment


def _close_stdin(process):
    try:
        process.stdin.close()
    except BrokenPipeError:
        pass


def run_plugin(code, request_data, timeout_seconds=None):
    if timeout_seconds is None:
        timeout_seconds = PLUGIN_TIMEOUT_SECONDS
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
        or timeout_seconds > MAX_PLUGIN_TIMEOUT_SECONDS
        or not math.isfinite(timeout_seconds)
    ):
        raise PluginExecutionError(
            f"Plugin timeout must be greater than 0 and no more than "
            f"{MAX_PLUGIN_TIMEOUT_SECONDS} seconds."
        )
    if not _PLUGIN_WORKER_SLOT.acquire(blocking=False):
        raise PluginWorkerBusy("A plugin worker is already running.")
    try:
        return _run_plugin(code, request_data, timeout_seconds)
    finally:
        _PLUGIN_WORKER_SLOT.release()


def _run_plugin(code, request_data, timeout_seconds):
    if len(code.encode("utf-8")) > MAX_PLUGIN_CODE_BYTES:
        raise PluginExecutionError("Plugin code exceeds the 64 KB limit.")
    try:
        request_bytes = json.dumps(
            request_data, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PluginExecutionError("Plugin input is not valid JSON.") from error
    if len(request_bytes) > MAX_PLUGIN_REQUEST_BYTES:
        raise PluginExecutionError("Plugin request exceeds the 64 KB limit.")
    try:
        input_data = json.dumps(
            {"code": code, "request": request_data},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PluginExecutionError("Plugin input is not valid JSON.") from error
    if len(input_data) > MAX_PLUGIN_CODE_BYTES + MAX_PLUGIN_REQUEST_BYTES + 1024:
        raise PluginExecutionError("Plugin input exceeds the allowed size.")

    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _WORKER_SOURCE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tempfile.gettempdir(),
            env=_clean_environment(),
            start_new_session=(os.name == "posix"),
        )
    except OSError as error:
        raise PluginExecutionError("Could not start the plugin worker.") from error

    stdout = bytearray()
    stderr = bytearray()
    exceeded = threading.Event()
    readers = [
        threading.Thread(
            target=_read_capped,
            args=(process.stdout, MAX_PLUGIN_OUTPUT_BYTES, stdout, exceeded),
            daemon=True,
        ),
        threading.Thread(
            target=_read_capped,
            args=(process.stderr, MAX_PLUGIN_STDERR_BYTES, stderr, exceeded),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    try:
        try:
            process.stdin.write(input_data)
            _close_stdin(process)
        except BrokenPipeError:
            pass

        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            if exceeded.is_set():
                _kill_process_tree(process)
                raise PluginOutputLimitExceeded("Plugin output exceeded the limit.")
            if time.monotonic() >= deadline:
                _kill_process_tree(process)
                raise PluginExecutionTimeout("Plugin exceeded the execution time limit.")
            time.sleep(0.025)
    finally:
        _kill_process_tree(process)
        process.wait()
        for reader in readers:
            reader.join(timeout=1)
        _close_stdin(process)
        process.stdout.close()
        process.stderr.close()

    if exceeded.is_set():
        raise PluginOutputLimitExceeded("Plugin output exceeded the limit.")
    if process.returncode:
        raise PluginExecutionError("Plugin execution failed.")
    try:
        result = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PluginExecutionError("Plugin returned an invalid JSON response.") from error
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("payload"), dict)
        or type(result.get("status_code")) is not int
        or not 200 <= result["status_code"] <= 599
    ):
        raise PluginExecutionError("Plugin returned an invalid response envelope.")
    return result["payload"], result["status_code"]
