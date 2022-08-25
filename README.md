# Installation

```console
pip install git+https://github.com/puddly/serialpy
```

# Usage

Serialpy features both a synchronous:

```Python
import serialpy

with serialpy.Serial("/dev/serial/by-id/port", baudrate=115200) as serial:
    data = serial.readexactly(5)
    serial.write(b"test")
```

And an asynchronous API superficially compatible with pyserial-asyncio:

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
```

The top-level constants are compatible with commonly used ones in both pyserial and pyserial-asyncio, allowing it to be a drop-in replacement for easy testing:

```Python
import serialpy as serial
import serialpy as serial_asyncio
```
