//! Gear-0 FFI — primitive hash/encode primitives for Celina's low-tier
//! inference pipeline. All exported functions are `extern "C"` and safe to
//! call from C/C++ hosts.
//!
//! Memory contract: every `*mut c_char` return value is allocated by Rust's
//! allocator (via `CString::into_raw()`). It MUST be released with
//! [`gear0_ffi_free`]. Passing it to libc's `free()` is undefined behaviour
//! because Rust's allocator and libc's allocator are not interchangeable.

use std::ffi::CStr;
use std::ffi::CString;
use std::os::raw::c_char;
use std::panic::catch_unwind;
use std::ptr;

use base64::Engine;
use sha2::Digest;

/// Maximum number of input bytes accepted by any FFI function.
/// Guards against unbounded input allocation attacks.
const MAX_FFI_INPUT_BYTES: usize = 64 * 1024; // 64 KiB

/// Trim leading/trailing ASCII whitespace from a byte slice.
fn trim_ws(bytes: &[u8]) -> &[u8] {
    let mut start = 0;
    let mut end = bytes.len();
    while start < end && matches!(bytes[start], b' ' | b'\t' | b'\n' | b'\r') {
        start += 1;
    }
    while end > start && matches!(bytes[end - 1], b' ' | b'\t' | b'\n' | b'\r') {
        end -= 1;
    }
    &bytes[start..end]
}

/// Allocate a C-compatible string from a `String`, returning a raw pointer.
/// The returned pointer must be freed with [`gear0_ffi_free`].
fn to_cstring(s: String) -> *mut c_char {
    match CString::new(s) {
        Ok(cstring) => cstring.into_raw(),
        Err(_) => ptr::null_mut(),
    }
}

/// Compute SHA-256 hash of input bytes.
///
/// Returns a null-terminated hex digest string allocated by Rust's allocator.
/// Caller MUST free with [`gear0_ffi_free`] — never with libc `free()`.
///
/// Input is trimmed of leading/trailing whitespace before hashing.
/// Inputs larger than [`MAX_FFI_INPUT_BYTES`] return a null pointer.
#[no_mangle]
pub extern "C" fn gear0_sha256(input: *const c_char) -> *mut c_char {
    match catch_unwind(|| {
        if input.is_null() {
            return ptr::null_mut();
        }
        let bytes = unsafe { CStr::from_ptr(input) }.to_bytes();
        if bytes.len() > MAX_FFI_INPUT_BYTES {
            return ptr::null_mut();
        }
        let trimmed = trim_ws(bytes);
        let digest = sha2::Sha256::digest(trimmed);
        let hex = format!("{:x}", digest);
        to_cstring(hex)
    }) {
        Ok(ptr) => ptr,
        Err(_) => ptr::null_mut(),
    }
}

/// Compute MD5 hash of input bytes.
///
/// # Security
///
/// MD5 is **not cryptographically secure** and must not be used for
/// signatures, password hashing, or any security-sensitive purpose. It is
/// provided only for legacy compatibility and non-security checksums.
///
/// Returns a null-terminated hex digest string allocated by Rust's allocator.
/// Caller MUST free with [`gear0_ffi_free`] — never with libc `free()`.
///
/// Input is trimmed of leading/trailing whitespace before hashing.
/// Inputs larger than [`MAX_FFI_INPUT_BYTES`] return a null pointer.
#[no_mangle]
pub extern "C" fn gear0_md5(input: *const c_char) -> *mut c_char {
    match catch_unwind(|| {
        if input.is_null() {
            return ptr::null_mut();
        }
        let bytes = unsafe { CStr::from_ptr(input) }.to_bytes();
        if bytes.len() > MAX_FFI_INPUT_BYTES {
            return ptr::null_mut();
        }
        let trimmed = trim_ws(bytes);
        let digest = md5::compute(trimmed);
        let hex = format!("{:x}", digest);
        to_cstring(hex)
    }) {
        Ok(ptr) => ptr,
        Err(_) => ptr::null_mut(),
    }
}

/// Base64-encode input bytes (RFC 4648 standard alphabet, no padding line wraps).
///
/// Returns a null-terminated base64 string allocated by Rust's allocator.
/// Caller MUST free with [`gear0_ffi_free`] — never with libc `free()`.
///
/// Input is trimmed of leading/trailing whitespace before encoding.
/// Inputs larger than [`MAX_FFI_INPUT_BYTES`] return a null pointer.
#[no_mangle]
pub extern "C" fn gear0_base64_encode(input: *const c_char) -> *mut c_char {
    match catch_unwind(|| {
        if input.is_null() {
            return ptr::null_mut();
        }
        let bytes = unsafe { CStr::from_ptr(input) }.to_bytes();
        if bytes.len() > MAX_FFI_INPUT_BYTES {
            return ptr::null_mut();
        }
        let trimmed = trim_ws(bytes);
        let encoded = base64::engine::general_purpose::STANDARD.encode(trimmed);
        to_cstring(encoded)
    }) {
        Ok(ptr) => ptr,
        Err(_) => ptr::null_mut(),
    }
}

/// Base64-decode input bytes (RFC 4648 standard alphabet).
///
/// Returns a null-terminated UTF-8 string containing the decoded text,
/// allocated by Rust's allocator. Returns null if input is invalid base64
/// or the decoded bytes are not valid UTF-8.
/// Caller MUST free with [`gear0_ffi_free`] — never with libc `free()`.
///
/// Input is trimmed of leading/trailing whitespace before decoding.
/// Inputs larger than [`MAX_FFI_INPUT_BYTES`] return a null pointer.
#[no_mangle]
pub extern "C" fn gear0_base64_decode(input: *const c_char) -> *mut c_char {
    match catch_unwind(|| {
        if input.is_null() {
            return ptr::null_mut();
        }
        let bytes = unsafe { CStr::from_ptr(input) }.to_bytes();
        if bytes.len() > MAX_FFI_INPUT_BYTES {
            return ptr::null_mut();
        }
        let trimmed = trim_ws(bytes);
        let decoded = match base64::engine::general_purpose::STANDARD.decode(trimmed) {
            Ok(b) => b,
            Err(_) => return ptr::null_mut(),
        };
        let decoded_str = match String::from_utf8(decoded) {
            Ok(s) => s,
            Err(_) => return ptr::null_mut(),
        };
        to_cstring(decoded_str)
    }) {
        Ok(ptr) => ptr,
        Err(_) => ptr::null_mut(),
    }
}

/// Free a string allocated by [`gear0_sha256`], [`gear0_md5`],
/// [`gear0_base64_encode`], or [`gear0_base64_decode`].
///
/// Safe to call with a null pointer. Uses Rust's allocator to deallocate —
/// do NOT pass the pointer to libc's `free()`.
#[no_mangle]
pub extern "C" fn gear0_ffi_free(s: *mut c_char) {
    let _ = catch_unwind(|| {
        if !s.is_null() {
            unsafe {
                let _ = CString::from_raw(s);
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trim_ws_removes_leading_and_trailing_whitespace() {
        assert_eq!(trim_ws(b"  hello  "), b"hello");
        assert_eq!(trim_ws(b"\t\nfoo\r\n"), b"foo");
        assert_eq!(trim_ws(b""), b"");
        assert_eq!(trim_ws(b"   \t\n\r  "), b"");
        assert_eq!(trim_ws(b"no-ws"), b"no-ws");
    }

    #[test]
    fn trim_ws_preserves_internal_whitespace() {
        assert_eq!(trim_ws(b"hello world"), b"hello world");
    }

    #[test]
    fn max_input_guard_rejects_oversized() {
        let big = vec![b'A'; MAX_FFI_INPUT_BYTES + 1];
        let c_str = CString::new(big).expect("vec contains no interior nul");
        let result = gear0_sha256(c_str.as_ptr());
        assert!(result.is_null(), "oversized input must return null");
        gear0_ffi_free(result); // null is safe
    }

    #[test]
    fn max_input_accepts_at_limit() {
        let at_limit = vec![b'A'; MAX_FFI_INPUT_BYTES];
        let c_str = CString::new(at_limit).expect("vec contains no interior nul");
        let result = gear0_sha256(c_str.as_ptr());
        assert!(!result.is_null(), "input at limit must succeed");
        gear0_ffi_free(result);
    }

    #[test]
    fn sha256_matches_known_vector() {
        let input = CString::new("hello").expect("no interior nul");
        let result = gear0_sha256(input.as_ptr());
        assert!(!result.is_null());
        let hex = unsafe { CStr::from_ptr(result) }.to_string_lossy().into_owned();
        gear0_ffi_free(result);
        assert_eq!(
            hex,
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        );
    }

    #[test]
    fn sha256_trims_whitespace() {
        let padded = CString::new("  hello  ").expect("no interior nul");
        let clean = CString::new("hello").expect("no interior nul");
        let padded_hex = {
            let r = gear0_sha256(padded.as_ptr());
            let h = unsafe { CStr::from_ptr(r) }.to_string_lossy().into_owned();
            gear0_ffi_free(r);
            h
        };
        let clean_hex = {
            let r = gear0_sha256(clean.as_ptr());
            let h = unsafe { CStr::from_ptr(r) }.to_string_lossy().into_owned();
            gear0_ffi_free(r);
            h
        };
        assert_eq!(padded_hex, clean_hex);
    }

    #[test]
    fn null_input_returns_null() {
        assert!(gear0_sha256(ptr::null()).is_null());
        assert!(gear0_md5(ptr::null()).is_null());
        assert!(gear0_base64_encode(ptr::null()).is_null());
        assert!(gear0_base64_decode(ptr::null()).is_null());
    }

    #[test]
    fn md5_matches_known_vector() {
        let input = CString::new("hello").expect("no interior nul");
        let result = gear0_md5(input.as_ptr());
        assert!(!result.is_null());
        let hex = unsafe { CStr::from_ptr(result) }.to_string_lossy().into_owned();
        gear0_ffi_free(result);
        assert_eq!(hex, "5d41402abc4b2a76b9719d911017c592");
    }

    #[test]
    fn base64_encode_decode_roundtrip() {
        let input = CString::new("hello, world! \u{1F600}").expect("no interior nul");
        let encoded_ptr = gear0_base64_encode(input.as_ptr());
        assert!(!encoded_ptr.is_null());
        let encoded =
            unsafe { CStr::from_ptr(encoded_ptr) }.to_string_lossy().into_owned();
        gear0_ffi_free(encoded_ptr);

        let encoded_c = CString::new(encoded.as_str()).expect("base64 has no interior nul");
        let decoded_ptr = gear0_base64_decode(encoded_c.as_ptr());
        assert!(!decoded_ptr.is_null());
        let decoded =
            unsafe { CStr::from_ptr(decoded_ptr) }.to_string_lossy().into_owned();
        gear0_ffi_free(decoded_ptr);
        assert_eq!(decoded, "hello, world! \u{1F600}");
    }

    #[test]
    fn base64_encode_matches_known_vector() {
        let input = CString::new("Hello, World!").expect("no interior nul");
        let result = gear0_base64_encode(input.as_ptr());
        assert!(!result.is_null());
        let encoded =
            unsafe { CStr::from_ptr(result) }.to_string_lossy().into_owned();
        gear0_ffi_free(result);
        assert_eq!(encoded, "SGVsbG8sIFdvcmxkIQ==");
    }

    #[test]
    fn base64_decode_invalid_returns_null() {
        let input = CString::new("!!!not valid base64!!!").expect("no interior nul");
        let result = gear0_base64_decode(input.as_ptr());
        assert!(result.is_null(), "invalid base64 must return null");
    }

    #[test]
    fn free_null_is_safe() {
        // Must not panic or crash
        gear0_ffi_free(ptr::null_mut());
    }

    #[test]
    fn catch_unwind_prevents_panic_propagation() {
        // All FFI functions use catch_unwind internally; calling with valid
        // input must not unwind across the ABI boundary.
        let input = CString::new("test").expect("no interior nul");
        let _ = gear0_sha256(input.as_ptr());
        let _ = gear0_md5(input.as_ptr());
        let _ = gear0_base64_encode(input.as_ptr());
        let _ = gear0_base64_decode(input.as_ptr());
    }
}
