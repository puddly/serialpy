import asyncio
import contextlib

import serialpy


EXPECTED_RSP = b'\xFE\x0A\x61\x02\x02\x01\x02\x07\x01\x36\x8B\x34\x01\x00\xE6'

async def main():
    reader, writer = await serialpy.open_serial_connection("/dev/cu.usbserial-1420", baudrate=115200)

    with contextlib.closing(writer):
        writer.write(b"\xFE\x00\x21\x02\x23")
        await writer.drain()
        rsp = await reader.readexactly(len(EXPECTED_RSP))
        assert rsp == EXPECTED_RSP, repr(rsp)


if __name__ == "__main__":
    asyncio.run(main())
