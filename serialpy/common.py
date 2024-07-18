import io
import dataclasses

from typing_extensions import Self


PARITY_NONE = None

STOPBITS_ONE = 1
STOPBITS_TWO = 2


@dataclasses.dataclass(frozen=True)
class ModemBits:
    le: bool | None = None
    dtr: bool | None = None
    rts: bool | None = None
    st: bool | None = None
    sr: bool | None = None
    cts: bool | None = None
    car: bool | None = None
    rng: bool | None = None
    dsr: bool | None = None


class BaseSerial(io.RawIOBase):
    def configure_port(self) -> None:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def path(self) -> str:
        raise NotImplementedError

    @property
    def baudrate(self) -> int:
        raise NotImplementedError

    def get_modem_bits(self) -> ModemBits:
        raise NotImplementedError

    def set_modem_bits(self, modem_bits: ModemBits) -> None:
        raise NotImplementedError

    @property
    def dtr(self) -> bool:
        return self.get_modem_bits().dtr

    @dtr.setter
    def dtr(self, value) -> None:
        self.set_modem_bits(ModemBits(dtr=bool(value)))

    @property
    def rts(self) -> bool:
        return self.get_modem_bits().rts

    @rts.setter
    def rts(self, value) -> None:
        self.set_modem_bits(ModemBits(rts=bool(value)))

    def close(self) -> None:
        raise NotImplementedError

    def readinto(self, b: bytearray) -> int:
        raise NotImplementedError

    def readexactly(self, n: int) -> bytes:
        buffer = bytearray(n)
        view = memoryview(buffer)
        remaining = n

        while remaining > 0:
            remaining -= self.readinto(view)

        return bytes(buffer)

    def write(self, data: bytes):
        raise NotImplementedError

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()
