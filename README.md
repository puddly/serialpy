# Introduction
Serialpy is a no-compromise serial communication library for Python targeting common
platforms such as Linux (POSIX), macOS, and Windows. It provides both synchronous and
native asynchronous APIs for all platforms.

# Installation

```console
pip install git+https://github.com/puddly/serialpy
```

# Usage

Serialpy features a pyserial and pyserial-asyncio compatibility layer for easy testing.
As early as possible, run `serialpy.patch()` and it will provide API-compatible replacements.

```python
import serialpy
serialpy.patch()

# These will now use serialpy
import serial
import serial_asyncio
```

Serialpy features a familiar synchronous API:

```Python
import serialpy

with serialpy.Serial("/dev/serial/by-id/port", baudrate=115200) as serial:
    data = serial.readexactly(5)
    serial.write(b"test")

    serial.set_modem_bits(rts=True, dtr=True)
    bits = serial.get_modem_bits()
    assert bits.rts is True
    assert bits.dtr is True
```

A high-level asynchronous serial `(reader, writer)` pair:

```Python
import asyncio
import contextlib

import serialpy

async def main():
	reader, writer = await serialpy.open_serial_connection("/dev/serial/by-id/port", baudrate=115200)

	with contextlib.closing(writer):
	    data = await reader.readexactly(5)
	    writer.write(b"test")
	    await writer.drain()
```

And a low-level asynchronous serial transport:

```Python
import asyncio
import serialpy

async def main():
	loop = asyncio.get_running_loop()
	protocol = YourProtocol()

	transport, protocol = await serialpy.create_serial_connection(
	    loop,
	    lambda: protocol,
	    url="/dev/serial/by-id/port",
	    baudrate=115200
	)

	await transport.set_modem_bits(rts=True, dtr=True)
```
