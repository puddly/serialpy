use crate::RustSerialPortInfo;
use serialport::{available_ports, SerialPortType};

pub fn list_serial_ports() -> Result<Vec<RustSerialPortInfo>, String> {
    let ports = available_ports().map_err(|e| e.to_string())?;

    Ok(ports
        .into_iter()
        .map(|port| {
            let (vid, pid, serial_number, manufacturer, product, interface_num) =
                match port.port_type {
                    SerialPortType::UsbPort(info) => (
                        Some(info.vid),
                        Some(info.pid),
                        info.serial_number,
                        info.manufacturer,
                        info.product,
                        info.interface,
                    ),
                    SerialPortType::PciPort
                    | SerialPortType::BluetoothPort
                    | SerialPortType::Unknown => (None, None, None, None, None, None),
                };

            RustSerialPortInfo {
                device: port.port_name,
                vid,
                pid,
                serial_number,
                manufacturer,
                product,
                bcd_device: None,
                interface_description: None,
                interface_num,
            }
        })
        .collect())
}
