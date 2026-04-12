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

Open a serial connection asynchronously:

```python
import serialx

reader, writer = await serialx.open_serial_connection(
    "/dev/serial/by-id/port",
    baudrate=115200,
)
```

For optional transports (ESPHome, RFC2217, socket) and compatibility notes, see
[Installation](./installation). For more patterns, see [Usage](./usage).
