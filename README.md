# Introduction
Serialx is a no-compromise serial communication library for Python targeting common
platforms such as Linux (POSIX), macOS, and Windows. It provides both synchronous and
native asynchronous APIs for all platforms.

# Installation

```console
pip install serialx
```

# Usage

Serialx features a pyserial and pyserial-asyncio compatibility layer for easy testing.
As early as possible, run `serialx.patch_pyserial()` and it will provide API-compatible
replacements.

```python
import serialx
serialx.patch_pyserial()

# These will now use serialx
import serial
import serial_asyncio
```

Serialx features a familiar synchronous API:

```Python
import serialx

with serialx.Serial("/dev/serial/by-id/port", baudrate=115200) as serial:
    data = serial.readexactly(5)
    serial.write(b"test")

    serial.set_modem_pins(rts=True, dtr=True)
    pins = serial.get_modem_pins()
    assert pins.rts is True
    assert pins.dtr is True
```

A high-level asynchronous serial `(reader, writer)` pair:

```Python
import asyncio
import contextlib

import serialx

async def main():
	reader, writer = await serialx.open_serial_connection("/dev/serial/by-id/port", baudrate=115200)

	with contextlib.closing(writer):
	    data = await reader.readexactly(5)
	    writer.write(b"test")
	    await writer.drain()
```

And a low-level asynchronous serial transport:

```Python
import asyncio
import serialx

async def main():
	loop = asyncio.get_running_loop()
	protocol = YourProtocol()

	transport, protocol = await serialx.create_serial_connection(
	    loop,
	    lambda: protocol,
	    url="/dev/serial/by-id/port",
	    baudrate=115200
	)

	await transport.set_modem_pins(rts=True, dtr=True)
```

# Development
All development dependencies are listed in `pyproject.toml`. To install them, use:
```bash
uv pip install '.[dev]'
```

On macOS and Windows, a Rust toolchain is required to build the native serial port
enumeration extension. Install Rust via [rustup](https://rustup.rs/).

Set up pre-commit hooks with `pre-commit install`. Your code will then be type checked
and auto-formatted when you run `git commit`. You can do this on-demand with
`pre-commit run`.

Serialx relies on automated testing. We do not have a custom GitHub Actions runners with
real hardware (yet) so GitHub Actions CI will only run `socat` tests.

To run tests locally with loopback adapters and pairs of real adapters, pass CLI flags
to `pytest`:

```bash
pytest --loopback-adapter=/dev/serial/by-id/my-loopback-adapter \
       --adapter-pair=/dev/serial/by-id/left1:/dev/serial/by-id/right1 \
       --adapter-pair=/dev/serial/by-id/left2:/dev/serial/by-id/right2
```

By default, this will run tests in parallel across all adapter pairs. You can disable
this by passing `-n 0` to `pytest`, which will execute the tests sequentially.
