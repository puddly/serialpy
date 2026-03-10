"""serialx constants."""

from __future__ import annotations

import dataclasses
from enum import Enum

from typing_extensions import Self


class StopBits(Enum):
    """Stop bits configuration."""

    ONE = 1
    ONE_POINT_FIVE = 1.5
    TWO = 2


class Parity(Enum):
    """Parity configuration."""

    NONE = None
    ODD = 1
    EVEN = 2
    MARK = 3
    SPACE = 4


class PinState(Enum):
    """Pin state."""

    UNDEFINED = None
    LOW = 0
    HIGH = 1

    @classmethod
    def convert(cls, value: PinState | bool | None) -> PinState:
        """Create PinState from boolean."""
        if isinstance(value, cls):
            return value

        if value is None:
            return cls.UNDEFINED

        return cls.HIGH if value else cls.LOW

    def to_bool(self) -> bool | None:
        """Convert PinState to boolean."""
        if self is PinState.UNDEFINED:
            return None

        return self is PinState.HIGH


@dataclasses.dataclass(frozen=True)
class ModemPins:
    """Modem control bits."""

    le: PinState = PinState.UNDEFINED
    dtr: PinState = PinState.UNDEFINED
    rts: PinState = PinState.UNDEFINED
    st: PinState = PinState.UNDEFINED
    sr: PinState = PinState.UNDEFINED
    cts: PinState = PinState.UNDEFINED
    car: PinState = PinState.UNDEFINED
    rng: PinState = PinState.UNDEFINED
    dsr: PinState = PinState.UNDEFINED

    @classmethod
    def all_off(cls) -> Self:
        """Create instance with all bits set to off."""
        return cls(
            le=PinState.LOW,
            dtr=PinState.LOW,
            rts=PinState.LOW,
            st=PinState.LOW,
            sr=PinState.LOW,
            cts=PinState.LOW,
            car=PinState.LOW,
            rng=PinState.LOW,
            dsr=PinState.LOW,
        )

    def __repr__(self) -> str:
        """Return string representation of modem pins."""

        bits = []

        for bit in (
            "le",
            "dtr",
            "rts",
            "st",
            "sr",
            "cts",
            "car",
            "rng",
            "dsr",
        ):
            value = getattr(self, bit)

            if value is PinState.UNDEFINED:
                continue
            elif value is PinState.HIGH:
                bits.append(bit)
            else:
                bits.append(f"!{bit}")

        return f"{self.__class__.__name__}[{' '.join(bits)}]"
