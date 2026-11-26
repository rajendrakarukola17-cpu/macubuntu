use std::ffi::CStr;
use std::ffi::CString;
use std::os::raw::c_char;
use std::ptr;

use base64::Engine;
use sha2::Digest;

/// Compute SHA-256 hash of a null-terminated UTF-8 string.
/// Returns a malloc'd null-terminated C string (hex digest).
/// Caller must free with gear0_ffi_free().
#[no_mangle]
pub extern "C" fn gear0_sha256(input: *const c_char) -> *mut c_char {
    if input.is_null() {
        return ptr::null_mut();
    }
    let input_str = match unsafe { CStr::from_ptr(input) }.to_str() {
        Ok(s) => s,
        Err(_) => return ptr::null_mut(),
    };
    let digest = sha2::Sha256::digest(input_str.as_bytes());
    let hex = format!("{:x}", digest);
    match CString::new(hex) {
        Ok(cstring) => cstring.into_raw(),
        Err(_) => ptr::null_mut(),
    }
}

/// Compute MD5 hash of a null-terminated UTF-8 string.
/// Returns a malloc'd null-terminated C string (hex digest).
/// Caller must free with gear0_ffi_free().
#[no_mangle]
pub extern "C" fn gear0_md5(input: *const c_char) -> *mut c_char {
    if input.is_null() {
        return ptr::null_mut();
    }
    let input_str = match unsafe { CStr::from_ptr(input) }.to_str() {
        Ok(s) => s,
        Err(_) => return ptr::null_mut(),
    };
    let digest = md5::compute(input_str.as_bytes());
    let hex = format!("{:x}", digest);
    match CString::new(hex) {
        Ok(cstring) => cstring.into_raw(),
        Err(_) => ptr::null_mut(),
    }
}

/// Base64-encode a null-terminated UTF-8 string.
/// Returns a malloc'd null-terminated C string.
/// Caller must free with gear0_ffi_free().
#[no_mangle]
pub extern "C" fn gear0_base64_encode(input: *const c_char) -> *mut c_char {
    if input.is_null() {
        return ptr::null_mut();
    }
    let input_str = match unsafe { CStr::from_ptr(input) }.to_str() {
        Ok(s) => s,
        Err(_) => return ptr::null_mut(),
    };
    let encoded = base64::engine::general_purpose::STANDARD.encode(input_str.as_bytes());
    match CString::new(encoded) {
        Ok(cstring) => cstring.into_raw(),
        Err(_) => ptr::null_mut(),
    }
}

/// Base64-decode a null-terminated UTF-8 string.
/// Returns a malloc'd null-terminated C string (decoded UTF-8 text).
/// Returns null if input is invalid base64 or not valid UTF-8.
/// Caller must free with gear0_ffi_free().
#[no_mangle]
pub extern "C" fn gear0_base64_decode(input: *const c_char) -> *mut c_char {
    if input.is_null() {
        return ptr::null_mut();
    }
    let input_str = match unsafe { CStr::from_ptr(input) }.to_str() {
        Ok(s) => s,
        Err(_) => return ptr::null_mut(),
    };
    let decoded = match base64::engine::general_purpose::STANDARD.decode(input_str) {
        Ok(bytes) => bytes,
        Err(_) => return ptr::null_mut(),
    };
    let decoded_str = match String::from_utf8(decoded) {
        Ok(s) => s,
        Err(_) => return ptr::null_mut(),
    };
    match CString::new(decoded_str) {
        Ok(cstring) => cstring.into_raw(),
        Err(_) => ptr::null_mut(),
    }
}

/// Free a string allocated by gear0_ffi_* functions.
/// Safe to call with null pointer.
#[no_mangle]
pub extern "C" fn gear0_ffi_free(s: *mut c_char) {
    if !s.is_null() {
        unsafe {
            let _ = CString::from_raw(s);
        }
    }
}
