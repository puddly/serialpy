# Migrating from pyserial

This page covers the main differences to be aware of when porting pyserial code to serialx.

## Packaging
serialx is API-compatible with both pyserial (`serial`) and pyserial-asyncio (`serial_asyncio`) at the same time.
You may be using a fork of pyserial-asyncio like pyserial-asyncio-fast. serialx replaces all three.

::::{tab-set}

:::{tab-item} requirements.txt
```diff
-pyserial
-pyserial-asyncio
-pyserial-asyncio-fast
+serialx
```
:::

:::{tab-item} pyproject.toml
```diff
 dependencies = [
-    "pyserial",
-    "pyserial-asyncio",
-    "pyserial-asyncio-fast",
+    "serialx",
 ]
```
:::

:::{tab-item} setup.py
```diff
 install_requires=[
-    "pyserial",
-    "pyserial-asyncio",
-    "pyserial-asyncio-fast",
+    "serialx",
 ],
```
:::

::::

## Sync migration
serialx provides compatibility properties, methods, and kwargs for almost all pyserial APIs.
Many are legacy methods and should be migrated to use modern names. All deprecated methods and properties
are listed in the [main API documentation](../api.md). The most common ones are listed below:

| Old Name                    | New Name                   | Notes                                        |
| ---                         | ---                        | ---                                          |
| `port`                      | `path`                     |                                              |
| `portstr`                   | `path`                     |                                              |
| `timeout`                   | `read_timeout`             |                                              |
| `writeTimeout`              | `write_timeout`            |                                              |
| `bytesize`                  | `byte_size`                |                                              |
| `data_bits`                 | `byte_size`                |                                              |
| `stop_bits`                 | `stopbits`                 | `stopbits` returns a `StopBits` enum         |
| `in_waiting`                | `num_unread_bytes()`       | Now a method                                 |
| `out_waiting`               | `num_unwritten_bytes()`    | Now a method                                 |
| `inWaiting()`               | `num_unread_bytes()`       |                                              |
| `isOpen()`                  | `is_open`                  | Now a property                               |
| `flushInput()`              | `reset_read_buffer()`      |                                              |
| `flushOutput()`             | `reset_write_buffer()`     |                                              |
| `reset_input_buffer()`      | `reset_read_buffer()`      |                                              |
| `reset_output_buffer()`     | `reset_write_buffer()`     |                                              |
| `Serial(port=...)`          | `serial_for_url(url, ...)` | Constructor kwarg                            |
| `Serial(timeout=...)`       | `read_timeout=...`         | Constructor kwarg                            |
| `Serial(writeTimeout=...)`  | `write_timeout=...`        | Constructor kwarg                            |
| `Serial(bytesize=...)`      | `byte_size=...`            | Constructor kwarg                            |
| `Serial(do_not_open=False)` | —                          | Not supported; open explicitly via `open()`  |
| `SerialPortInfo[i]`         | attribute access           | Slicing `SerialPortInfo` is deprecated       |
| `SerialPortInfo.description`| `SerialPortInfo.product`   |                                              |

### Connecting to a serial port
Use `serialx.serial_for_url(url, ...)` instead of `serial.Serial(port, ...)`.

`serial_for_url` automatically dispatches to native serial devices, `socket://`, `rfc2217://`, `esphome://`, all future platform handlers, allowing your code to work with serial ports of any type without any modification. `serial.Serial`, on the other hand, only connects to a single type of device. Its direct construction is deprecated.

```python
import serialx

with serialx.serial_for_url("/dev/ttyUSB0", baudrate=115200) as serial:
    serial.write(b"ping")
```

There is no equivalent for async code because the default `create_serial_connection` and `open_serial_connection` functions already transparently accept URIs.

## Exceptions
pyserial re-raises all errors as `serial.SerialException`, including OS-level failures and timeouts. serialx raises native exceptions directly so callers can handle them granularly:

| Condition                    | pyserial                        | serialx                                   |
| ---                          | ---                             | ---                                       |
| Device missing               | `SerialException`               | `FileNotFoundError` or another `OSError`  |
| I/O error                    | `SerialException`               | `OSError`                                 |
| Protocol/backend failure     | `SerialException`               | `serialx.SerialException` (subclass)      |
| Read/write timeout           | `SerialTimeoutException`        | `TimeoutError`                            |
| Unsupported setting          | `ValueError` / `SerialException`| `serialx.UnsupportedSetting`              |
| Unknown URL scheme           | —                               | `serialx.UnknownUriScheme`                |

## Async migration
Existing packages like pyserial-asyncio and pyserial-asyncio-fast have a straightforward public API: `create_serial_connection` and `open_serial_connection` allow you to interact with a `SerialTransport`. serialx has the same public API and exposes the same functions, just replace the imports.

### Reading and writing modem pins
If your async code accesses `transport.serial` to interact with modem pins, it is performing blocking IO on the event loop.

:::{danger} ❌ Don't do blocking IO in the event loop
```python
transport.serial.dtr = True

if transport.serial.cts:
    ...
```
:::

Instead, use the new async APIs to read and write pins:

:::{tip} ✅ Use async APIs instead
```python
await transport.set_modem_pins(dtr=True, rts=False)

pins = await transport.get_modem_pins()
if pins.cts is serialx.PinState.HIGH:
    ...
```
:::

`set_modem_pins` accepts individual pin kwargs or a full `ModemPins` dataclass. Pins omitted from the call are left unchanged. `get_modem_pins` returns a `ModemPins` dataclass of `PinState` enum values, call `.to_bool()` on a pin for a `bool | None`.
