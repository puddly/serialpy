import { loadPyodide } from "pyodide";
import path from "node:path";
import process from "node:process";

import { createFakeSerialPair } from "./fake_serial";

declare global {
  var create_fake_serial_pair: typeof createFakeSerialPair;
}

const HERE = import.meta.dir;
const REPO_ROOT = path.resolve(HERE, "../../..");

globalThis.create_fake_serial_pair = createFakeSerialPair;

const pyodide = await loadPyodide();
await pyodide.loadPackage([
  "pytest",
  "pytest-asyncio",
  "typing-extensions",
  "async-timeout",
  "micropip",
]);

await pyodide.runPythonAsync(`
import signal
signal.setitimer = lambda which, seconds, interval=0.0: (0.0, 0.0)

import micropip
micropip.add_mock_package("psutil", "0.0.0")
await micropip.install("pytest-timeout")
`);

pyodide.FS.mkdir("/repo");
pyodide.mountNodeFS("/repo", REPO_ROOT);

const sitePackages = pyodide.runPython(
  "import site; site.getsitepackages()[0]",
) as string;
pyodide.FS.symlink("/repo/serialx", `${sitePackages}/serialx`);

const pytestArgs = process.argv.slice(2);
if (pytestArgs.length === 0) {
  pytestArgs.push("/repo/tests/test_serial_pyodide.py");
}

pyodide.globals.set("pytest_argv", pytestArgs);

const rc = (await pyodide.runPythonAsync(`
import pytest
int(pytest.main([
    "--override-ini=addopts=",
    "-W", "ignore::DeprecationWarning:pytest_asyncio.plugin",
    *pytest_argv,
]))
`)) as number;

process.exit(rc);
