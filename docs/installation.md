---
icon: lucide/download
---

# Installation
Install the Python package with your package manager of choice:
```bash
pip install serialx
```

If you want to take advantage of the ESPHome transport, you can install the optional dependencies:
```bash
pip install 'serialx[esphome]'
```

# Compatibility
serialx provides a backwards-compatibility module that exposes pyserial, pyserial-asyncio, and pyserial-asyncio-fast compatible constants and methods. It aims to be a drop-in replacement for existing code.

Make sure to uninstall the existing modules, as Python packaging tools will merge the module trees otherwise:
```bash
pip uninstall pyserial pyserial-asyncio pyserial-asyncio-fast
pip install serialx-compat  # or serialx-compat[esphome]
```

If you are using uv, you can also use `--excludes` to do the same:
```bash
printf "pyserial\npyserial-asyncio\npyserial-asyncio-fast" > requirements_excludes.txt
uv pip install -r requirements.txt --excludes requirements_excludes.txt
```
