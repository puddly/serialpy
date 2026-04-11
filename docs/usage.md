---
icon: lucide/code-2
---

# Usage
serialx supports both synchronous and asynchronous APIs on all platforms.

# Sync
```python
import serialx

with serialx.serial_for_url("/dev/serial/by-id/port", baudrate=115200) as serial:
    data = serial.readexactly(5)
    serial.write(b"test")
```

# Async (`StreamReader` and `StreamWriter`)
```python
import serialx

reader, writer = await serialx.open_serial_connection(
    "/dev/serial/by-id/port",
    baudrate=115200,
)

try:
    data = await reader.readexactly(5)
    writer.write(b"test")
    await writer.drain()
finally:
    writer.close()
    await writer.wait_closed()
```

# Async (transport)
```python
import asyncio
import serialx

loop = asyncio.get_running_loop()
transport, protocol = await serialx.create_serial_connection(
    loop=loop,
    protocol_factory=your_protocol_factory,
    url="/dev/serial/by-id/port",
    baudrate=115200,
)
```
