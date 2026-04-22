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

int(pytest.main([
    "--override-ini=addopts=",
    "-W", "ignore::DeprecationWarning:pytest_asyncio.plugin",
    *pytest_argv,
]))
`)) as number;

process.exit(rc);
