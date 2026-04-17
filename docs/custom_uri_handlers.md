# Custom URI handlers
serialx dispatches URLs like `/dev/ttyUSB0`, `socket://host:port`, or
`rfc2217://host:port` through a registration-based handler system. You can
register your own scheme so that `serialx.serial_for_url(...)` and the async
entry points route to your sync class and async transport.

## Registering a handler
Call {func}`serialx.register_uri_handler` at module import time:

```python
from serialx import BaseSerial, BaseSerialTransport, register_uri_handler


class MySerial(BaseSerial):
    """Synchronous implementation."""
    # ... implement the BaseSerial interface ...


class MySerialTransport(BaseSerialTransport):
    """Async transport."""
    # ... implement the BaseSerialTransport interface ...


register_uri_handler(
    scheme="my-backend://",
    unique_scheme="my-backend://",
    sync_cls=MySerial,
    async_transport_cls=MySerialTransport,
)
```

After registration, any call to `serial_for_url("my-backend://...")` or
`create_serial_connection(url="my-backend://...")` dispatches to your classes.

## Parameters
`scheme` is the dispatch scheme. Multiple handlers may share the same
`scheme` but the highest-`weight` registration wins. This is how the `device://`
scheme is shared across the platform-native handlers (`linux://`, `darwin://`,
`windows://`, and so on): they all register under `scheme="device://"` but
with a distinct `unique_scheme`.

`unique_scheme` is an address for a specific handler. It lets callers
bypass the shared-scheme dispatch and force a specific backend:

```python
serialx.serial_for_url("posix:///dev/ttyUSB0")  # forces the generic POSIX handler
```

`weight` controls priority under a shared `scheme`. The platform-native
handlers use `weight=3`, the extended POSIX handler uses `weight=2`, and the
bare POSIX handler uses the default `weight=1`. You rarely need to set this
unless you are layering a custom backend over an existing shared scheme.

`strip_uri_scheme` is a convenience for backends whose underlying class
expects a bare device path rather than a URI. When set, the leading
`scheme` / `unique_scheme` is removed before the URI reaches
`sync_cls.__init__` or `transport.connect(path=...)`. The platform `device://`
handlers use this so that `posix:///dev/ttyUSB0` is passed to `PosixSerial`
as `/dev/ttyUSB0`. Socket-style backends (`socket://`, `rfc2217://`,
`esphome://`) leave this `False` because the class parses the full URI for
host, port, and query parameters.

`list_serial_ports_func` is a callable that enumerates ports for this backend.

## Unregistering

`register_uri_handler` returns a callable that removes the registration:

```python
unregister = register_uri_handler(...)
# ... later ...
unregister()
```
