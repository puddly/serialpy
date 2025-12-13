use pyo3::prelude::*;
use serialport::{available_ports, SerialPortType};

/// (port_name, vid, pid, serial_number, manufacturer, product)
type PortInfo = (
    String,
    Option<u16>,
    Option<u16>,
    Option<String>,
    Option<String>,
    Option<String>,
);

#[pyfunction]
fn list_serial_ports_impl() -> PyResult<Vec<PortInfo>> {
    let ports = available_ports()
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyOSError, _>(format!("{e}")))?;

    Ok(ports
        .into_iter()
        .map(|p| {
            let (vid, pid, sn, mfr, prod) = match p.port_type {
                SerialPortType::UsbPort(u) => (
                    Some(u.vid),
                    Some(u.pid),
                    u.serial_number,
                    u.manufacturer,
                    u.product,
                ),
                SerialPortType::PciPort => (None, None, None, None, None),
                SerialPortType::BluetoothPort => (None, None, None, None, None),
                SerialPortType::Unknown => (None, None, None, None, None),
            };

            (p.port_name, vid, pid, sn, mfr, prod)
        })
        .collect())
}

#[pymodule]
fn _serialx_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(list_serial_ports_impl, m)?)?;
    Ok(())
}
