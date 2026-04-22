// Run a subset of the serialx pytest suite inside Pyodide under Bun.
//
// Usage:
//   bun run run_tests.mjs                     # runs tests/test_serial_pyodide.py
//   bun run run_tests.mjs <pytest-args>...    # forwards args to pytest.main
import { loadPyodide } from "pyodide";
import path from "node:path";
import process from "node:process";

const HERE = import.meta.dir;
const REPO_ROOT = path.resolve(HERE, "../../..");

const pyodide = await loadPyodide();

await pyodide.loadPackage([
  "pytest",
  "pytest-asyncio",
  "typing-extensions",
  "async-timeout",
]);

// Mount the repo read-through so source edits are picked up without copying.
pyodide.FS.mkdir("/repo");
pyodide.mountNodeFS("/repo", REPO_ROOT);

// Symlink the serialx package into site-packages so `import serialx` works
// naturally, without any sys.path gymnastics inside the tests. Also expose our
// psutil shim there so tests/common.py's top-level `import psutil` succeeds.
const sitePackages = pyodide.runPython("import site; site.getsitepackages()[0]");
pyodide.FS.symlink("/repo/serialx", `${sitePackages}/serialx`);
pyodide.FS.symlink(
  "/repo/tests/data/pyodide/stubs/psutil.py",
  `${sitePackages}/psutil.py`,
);

const pytestArgs = process.argv.slice(2);
if (pytestArgs.length === 0) {
  pytestArgs.push("/repo/tests/test_serial_pyodide.py");
}

pyodide.globals.set("pytest_argv", pytestArgs);

const rc = pyodide.runPython(`
import pytest
# pyproject.toml's addopts uses pytest-xdist (-n auto), which isn't available
# under Pyodide; drop all addopts for this run.
int(pytest.main([
    "--override-ini=addopts=",
    *pytest_argv,
]))
`);

process.exit(rc);
