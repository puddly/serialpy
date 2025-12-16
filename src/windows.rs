//! Windows SetupAPI-based serial port enumeration.

use crate::RustSerialPortInfo;
use std::collections::HashMap;
use std::ffi::OsString;
use std::os::windows::prelude::OsStringExt;
use windows::core::w;
use windows::core::GUID;
use windows::Win32::Devices::DeviceAndDriverInstallation::{
    SetupDiDestroyDeviceInfoList, SetupDiEnumDeviceInfo, SetupDiGetClassDevsW,
    SetupDiGetDeviceRegistryPropertyW, SetupDiOpenDevRegKey, DICS_FLAG_GLOBAL,
    DIGCF_DEVICEINTERFACE, DIGCF_PRESENT, DIREG_DEV, HDEVINFO, SETUP_DI_REGISTRY_PROPERTY,
    SPDRP_HARDWAREID, SP_DEVINFO_DATA,
};
use windows::Win32::System::Registry::{RegCloseKey, RegQueryValueExW, KEY_READ, REG_VALUE_TYPE};

// GUID_DEVINTERFACE_COMPORT: {86E0D1E0-8089-11D0-9CE4-08003E301F73}
const GUID_DEVINTERFACE_COMPORT: GUID = GUID::from_u128(0x86E0D1E0_8089_11D0_9CE4_08003E301F73);

struct ScopedHDevInfo(HDEVINFO);

impl Drop for ScopedHDevInfo {
    fn drop(&mut self) {
        unsafe {
            let _ = SetupDiDestroyDeviceInfoList(self.0);
        }
    }
}

pub fn list_serial_ports() -> Result<Vec<RustSerialPortInfo>, String> {
    let mut results = Vec::new();

    unsafe {
        let hdevinfo = SetupDiGetClassDevsW(
            Some(&GUID_DEVINTERFACE_COMPORT),
            None,
            None,
            DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
        )
        .map_err(|e| format!("SetupDiGetClassDevsW failed: {}\n", e))?;

        let _scope = ScopedHDevInfo(hdevinfo);

        let mut index = 0;
        let mut devinfo_data = SP_DEVINFO_DATA {
            cbSize: std::mem::size_of::<SP_DEVINFO_DATA>() as u32,
            ..Default::default()
        };

        while SetupDiEnumDeviceInfo(hdevinfo, index, &mut devinfo_data).is_ok() {
            index += 1;

            if let Some(device) = get_port_name(hdevinfo, &devinfo_data) {
                let hardware_ids =
                    get_property_multi_string(hdevinfo, &devinfo_data, SPDRP_HARDWAREID);

                let (vid, pid) = parse_hardware_id(&hardware_ids);

                results.push(RustSerialPortInfo {
                    device,
                    vid,
                    pid,
                    serial_number: None,
                    manufacturer: None,
                    product: None,
                    bcd_device: None,
                    interface: None,
                });
            }
        }
    }

    Ok(results)
}

unsafe fn get_property_multi_string(
    hdevinfo: HDEVINFO,
    devinfo_data: &SP_DEVINFO_DATA,
    property: SETUP_DI_REGISTRY_PROPERTY,
) -> Vec<String> {
    let mut required_size = 0;
    let _ = SetupDiGetDeviceRegistryPropertyW(
        hdevinfo,
        devinfo_data,
        property,
        None,
        None,
        Some(&mut required_size),
    );

    if required_size == 0 {
        return Vec::new();
    }

    let mut buffer = vec![0u8; required_size as usize];
    if SetupDiGetDeviceRegistryPropertyW(
        hdevinfo,
        devinfo_data,
        property,
        None,
        Some(&mut buffer),
        None,
    )
    .is_err()
    {
        return Vec::new();
    }

    let u16_vec: Vec<u16> = buffer
        .chunks_exact(2)
        .map(|chunk| u16::from_le_bytes([chunk[0], chunk[1]]))
        .collect();

    u16_vec
        .split(|&c| c == 0)
        .filter(|chunk| !chunk.is_empty())
        .filter_map(|chunk| OsString::from_wide(chunk).into_string().ok())
        .collect()
}

unsafe fn get_port_name(hdevinfo: HDEVINFO, devinfo_data: &SP_DEVINFO_DATA) -> Option<String> {
    let hkey = SetupDiOpenDevRegKey(
        hdevinfo,
        devinfo_data,
        DICS_FLAG_GLOBAL.0,
        0,
        DIREG_DEV,
        KEY_READ.0,
    )
    .ok()?;

    let value_name = w!("PortName");
    let mut data_type = REG_VALUE_TYPE::default();
    let mut size = 0u32;

    // First call to get size
    let _ = RegQueryValueExW(
        hkey,
        value_name,
        None,
        Some(&mut data_type),
        None,
        Some(&mut size),
    );

    if size == 0 {
        let _ = RegCloseKey(hkey);
        return None;
    }

    let mut buffer = vec![0u16; (size as usize) / 2];
    let result = RegQueryValueExW(
        hkey,
        value_name,
        None,
        Some(&mut data_type),
        Some(buffer.as_mut_ptr() as *mut u8),
        Some(&mut size),
    );

    let _ = RegCloseKey(hkey);

    if result.is_err() {
        return None;
    }

    // Trim null terminator
    let len = buffer.iter().position(|&c| c == 0).unwrap_or(buffer.len());
    OsString::from_wide(&buffer[..len]).into_string().ok()
}

fn parse_hardware_id(hardware_ids: &[String]) -> (Option<u16>, Option<u16>) {
    for hwid in hardware_ids {
        let props: HashMap<&str, &str> = hwid
            .split(|c: char| c == '\\' || c == '&')
            .filter_map(|part| part.split_once('_'))
            .collect();

        let vid = props
            .get("VID")
            .and_then(|v| u16::from_str_radix(v, 16).ok());
        let pid = props
            .get("PID")
            .and_then(|v| u16::from_str_radix(v, 16).ok());

        if vid.is_some() || pid.is_some() {
            return (vid, pid);
        }
    }
    (None, None)
}
