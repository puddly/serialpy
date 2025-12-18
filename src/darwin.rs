//! macOS IOKit-based serial port enumeration.
//! Note: io-kit-sys bindings incorrectly use `*mut c_char` where `*const c_char` should be used.
//! The IOKit functions don't actually mutate these strings, so the casts are safe.
#![allow(clippy::as_ptr_cast_mut)]

use std::ffi::CStr;

use core_foundation::base::{kCFAllocatorDefault, CFType, TCFType};
use core_foundation::number::CFNumber;
use core_foundation::string::CFString;
use io_kit_sys::ret::kIOReturnSuccess;
use io_kit_sys::types::{io_iterator_t, io_object_t};
use io_kit_sys::{
    kIOMasterPortDefault, kIORegistryIterateParents, kIORegistryIterateRecursively, IOIteratorNext,
    IOObjectRelease, IOObjectRetain, IORegistryEntryCreateCFProperty,
    IORegistryEntrySearchCFProperty, IOServiceGetMatchingServices, IOServiceMatching,
};

use crate::RustSerialPortInfo;

const KIO_SERVICE_PLANE: &CStr = c"IOService";

/// RAII wrapper for IOKit objects.
struct IoObject(io_object_t);

impl IoObject {
    const fn from_raw(obj: io_object_t) -> Self {
        Self(obj)
    }

    const fn raw(&self) -> io_object_t {
        self.0
    }

    fn string_property(&self, key: &str) -> Option<String> {
        let cf_key = CFString::new(key);
        let value = unsafe {
            IORegistryEntryCreateCFProperty(
                self.0,
                cf_key.as_concrete_TypeRef() as _,
                kCFAllocatorDefault,
                0,
            )
        };

        if value.is_null() {
            return None;
        }

        let cf_type: CFType = unsafe { TCFType::wrap_under_create_rule(value) };
        cf_type.downcast::<CFString>().map(|s| s.to_string())
    }

    fn search_parent_property(&self, key: &str) -> Option<CFType> {
        let cf_key = CFString::new(key);
        let value = unsafe {
            IORegistryEntrySearchCFProperty(
                self.0,
                KIO_SERVICE_PLANE.as_ptr() as *mut _,
                cf_key.as_concrete_TypeRef() as _,
                kCFAllocatorDefault,
                kIORegistryIterateRecursively | kIORegistryIterateParents,
            )
        };

        if value.is_null() {
            return None;
        }

        Some(unsafe { TCFType::wrap_under_create_rule(value) })
    }

    fn search_parent_string_property(&self, key: &str) -> Option<String> {
        self.search_parent_property(key)?
            .downcast::<CFString>()
            .map(|s| s.to_string())
    }

    fn search_parent_u16_property(&self, key: &str) -> Option<u16> {
        self.search_parent_property(key)?
            .downcast::<CFNumber>()
            .and_then(|n| n.to_i64().map(|i| i as u16))
    }
}

impl Clone for IoObject {
    fn clone(&self) -> Self {
        if self.0 != 0 {
            unsafe { IOObjectRetain(self.0) };
        }
        Self(self.0)
    }
}

impl Drop for IoObject {
    fn drop(&mut self) {
        if self.0 != 0 {
            unsafe { IOObjectRelease(self.0) };
        }
    }
}

/// Iterator wrapper for IOKit iterators.
struct IoIterator(IoObject);

impl IoIterator {
    const fn from_raw(iter: io_iterator_t) -> Self {
        Self(IoObject::from_raw(iter))
    }
}

impl Iterator for IoIterator {
    type Item = IoObject;

    fn next(&mut self) -> Option<Self::Item> {
        let obj = unsafe { IOIteratorNext(self.0.raw()) };
        (obj != 0).then(|| IoObject::from_raw(obj))
    }
}

/// List all serial ports using IOKit.
pub fn list_serial_ports() -> Result<Vec<RustSerialPortInfo>, String> {
    let matching = unsafe { IOServiceMatching(c"IOSerialBSDClient".as_ptr()) };
    if matching.is_null() {
        return Err("IOServiceMatching returned null".into());
    }

    let mut iterator_raw: io_iterator_t = 0;
    let kr =
        unsafe { IOServiceGetMatchingServices(kIOMasterPortDefault, matching, &mut iterator_raw) };
    if kr != kIOReturnSuccess {
        return Err(format!("IOServiceGetMatchingServices failed: {}", kr));
    }

    let iterator = IoIterator::from_raw(iterator_raw);

    Ok(iterator
        .filter_map(|service| get_serial_port_info(&service))
        .collect())
}

/// Get info for a single serial port service.
fn get_serial_port_info(service: &IoObject) -> Option<RustSerialPortInfo> {
    let device = service.string_property("IOCalloutDevice")?;

    // Check if it's a USB device by searching for idVendor up the tree.
    if let Some(vid) = service.search_parent_u16_property("idVendor") {
        Some(RustSerialPortInfo {
            device,
            vid: Some(vid),
            pid: service.search_parent_u16_property("idProduct"),
            serial_number: service.search_parent_string_property("kUSBSerialNumberString"),
            manufacturer: service.search_parent_string_property("kUSBVendorString"),
            product: service.search_parent_string_property("kUSBProductString"),
            bcd_device: service.search_parent_u16_property("bcdDevice"),
            interface_description: service.search_parent_string_property("kUSBString"),
            interface_num: service
                .search_parent_u16_property("bInterfaceNumber")
                .map(|n| n as u8),
        })
    } else {
        // Non-USB serial port (Bluetooth, native, etc.)
        Some(RustSerialPortInfo {
            device,
            vid: None,
            pid: None,
            serial_number: None,
            manufacturer: None,
            product: None,
            bcd_device: None,
            interface_description: None,
            interface_num: None,
        })
    }
}
