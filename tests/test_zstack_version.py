import serialpy

if __name__ == "__main__":
    EXPECTED_RSP = b'\xFE\x0A\x61\x02\x02\x01\x02\x07\x01\x36\x8B\x34\x01\x00\xE6'

    with serialpy.Serial("/dev/cu.usbserial-1420", baudrate=115200) as serial:
        serial.set_modem_bits(serialpy.ModemBits.all_off())

        assert serial.get_modem_bits() == serialpy.ModemBits.from_int(0)

        serial.write(b"\xFE\x00\x21\x02\x23")
        rsp = serial.read(len(EXPECTED_RSP))
        assert rsp == EXPECTED_RSP, repr(rsp)

        serial.set_modem_bits(serialpy.ModemBits(rts=True))
        assert serial.get_modem_bits().rts is True
        serial.set_modem_bits(serialpy.ModemBits(rts=False))
        assert serial.get_modem_bits().rts is False
