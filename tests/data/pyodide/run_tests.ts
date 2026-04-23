import { loadPyodide } from "pyodide";
import path from "node:path";

import { createFakeSerialPair } from "./fake_serial";

declare global {
  var create_fake_serial_pair: typeof createFakeSerialPair;
}

globalThis.create_fake_serial_pair = createFakeSerialPair;

const pyodide = await loadPyodide();
await pyodide.loadPackage([
  "pytest",
  "pytest-asyncio",
  "typing-extensions",
  "async-timeout",
  "micropip",
  "sqlite3",
]);

pyodide.FS.mkdir("/repo");
pyodide.mountNodeFS("/repo", path.resolve(import.meta.dir, "../../.."));

const args = process.argv.slice(2);
pyodide.globals.set("pytest_argv", args.length ? args : ["/repo/tests"]);

const rc = (await pyodide.runPythonAsync(`
import os
import site
import sys
import pytest
import signal
import micropip


# Push .coverage to the host FS
os.chdir("/repo")
os.symlink("/repo/serialx", f"{site.getsitepackages()[0]}/serialx")

# Stub implementation of signal.setitimer
signal.setitimer = lambda which, seconds, interval=0.0: (0.0, 0.0)

micropip.add_mock_package("psutil", "0.0.0")
await micropip.install(["pytest-timeout", "pytest-cov"])

# XXX: coverage breaks unless restart_events() is called after SysMonitor.start()
from coverage.sysmon import SysMonitor
_orig_sysmonitor_start = SysMonitor.start

def _sysmonitor_start_with_restart(self):
    _orig_sysmonitor_start(self)
    sys.monitoring.restart_events()

SysMonitor.start = _sysmonitor_start_with_restart

os.environ["COVERAGE_CORE"] = "sysmon"

int(pytest.main([
    "--override-ini=addopts=",
    "-W", "ignore::DeprecationWarning:pytest_asyncio.plugin",
    *pytest_argv,
]))
`)) as number;

process.exit(rc);
