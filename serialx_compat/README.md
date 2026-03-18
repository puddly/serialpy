# serialx-compat

`serialx-compat` provides import-path compatibility for projects that expect:

- `serial`
- `serial_asyncio`
- `serial_asyncio_fast`

All functionality is delegated to [`serialx`](https://pypi.org/project/serialx/).

## Installation

```bash
pip install serialx-compat
```

From this monorepo via Git:

```bash
uv pip install "git+https://github.com/puddly/serialx.git@<ref>#subdirectory=serialx_compat"
```

## Usage

```python
import serial
import serial_asyncio
import serial_asyncio_fast
```

These modules are backed by `serialx` implementations.

## Notes

This package intentionally overlaps Python import paths with `pyserial`,
`pyserial-asyncio`, and `pyserial-asyncio-fast`. Do not install them together.
