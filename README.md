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
