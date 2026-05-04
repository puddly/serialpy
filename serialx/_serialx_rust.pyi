"""Type stubs for the optional Rust extension."""

class RustSerialPortInfo:
    device: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    manufacturer: str | None
    product: str | None
    bcd_device: int | None
    interface_description: str | None
    interface_num: int | None

def list_serial_ports_impl() -> list[RustSerialPortInfo]: ...
