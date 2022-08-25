from __future__ import annotations

import asyncio
import logging
import urllib.parse

from serialpy.serial import Serial, STOPBITS_ONE, PARITY_NONE
from serialpy.descriptor_transport import DescriptorTransport


LOGGER = logging.getLogger(__name__)


class SerialTransport(DescriptorTransport):
    transport_name = "serial"

    def __init__(
        self,
        loop,
        protocol,
        path,
        baudrate,
        stopbits=STOPBITS_ONE,
        xonxoff=False,
        rtscts=False,
        waiter=None,
        extra=None,
    ):
        super().__init__(loop, protocol, path, waiter, extra)
        self._serial = Serial(
            path=path,
            baudrate=baudrate,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            # `DescriptorTransport` opened the port
            fileno=self._fileno,
        )
        self._serial.configure_port()
        self._extra["serial"] = self._serial

    @property
    def serial(self):
        return self._serial


async def create_serial_connection(
    loop,
    protocol_factory,
    url,
    baudrate,
    parity=PARITY_NONE,
    stopbits=STOPBITS_ONE,
    xonxoff=False,
    rtscts=False,
    *,
    transport_factory=SerialTransport,
):
    parsed_path = urllib.parse.urlparse(url)

    if parsed_path.scheme in ("socket", "tcp"):
        transport, protocol = await loop.create_connection(
            lambda: protocol, parsed_path.hostname, parsed_path.port
        )
    else:
        protocol = protocol_factory()
        transport = transport_factory(
            loop=loop,
            protocol=protocol,
            path=url,
            baudrate=baudrate,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
        )

    return transport, protocol
