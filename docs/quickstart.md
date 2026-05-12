# Quickstart
Install `serialx`:

```bash
pip install serialx
```

Open a serial port synchronously:

```python
import serialx

with serialx.serial_for_url("/dev/serial/by-id/port", baudrate=115200) as serial:
    serial.write(b"ping")
    data = serial.readexactly(4)
```

Open a serial port asynchronously:

```python
import serialx

async with serialx.async_serial_for_url(
    "/dev/serial/by-id/port", baudrate=115200,
) as serial:
    await serial.write(b"ping")
    data = await serial.readexactly(4)
```

A `(StreamReader, StreamWriter)` pair is also available for code already wired up to
the asyncio streams API:

```Python
import asyncio
import serialx

async def main():
    reader, writer = await serialx.open_serial_connection(
        "/dev/serial/by-id/port", baudrate=115200,
    )

    try:
        data = await reader.readexactly(5)
        writer.write(b"test")
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()
```

And a low-level asynchronous serial transport for protocol-style consumers:

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
        baudrate=115200,
    )

    await transport.set_modem_pins(rts=True, dtr=True)
```

For optional transports (ESPHome, RFC2217, socket) and compatibility notes, see
[Installation](./installation). For more patterns, see [Usage](./usage).
