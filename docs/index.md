# serialx
serialx is a serial communication library for Python with both synchronous and native asynchronous APIs across Linux, macOS, and Windows.
It supports a variety of URI schemes to communicate with various serial backends:

- Traditional serial ports: `/dev/serial/by-id/port`, `COM1`, etc.
- TCP (e.g. socat or ser2net): `socket://host:port`
- RFC2217: `rfc2217://host:port`
- ESPHome: `esphome://host:port?port_name=Serial+1`

Each backend has protocol-specific options that can be passed as keyword arguments to the serial constructor or as URI parameters.

```{toctree}
:maxdepth: 2
:hidden:

quickstart
installation
usage
custom_uri_handlers
api
```

```{toctree}
:caption: Development
:hidden:

development
```
