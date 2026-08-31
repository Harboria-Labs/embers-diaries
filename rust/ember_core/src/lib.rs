use pyo3::prelude::*;
use sha2::{Digest, Sha256};

#[pyfunction]
fn sha256_hex(canonical_bytes: &[u8]) -> String {
    let digest = Sha256::digest(canonical_bytes);
    format!("{digest:x}")
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(sha256_hex, module)?)?;
    module.add("BACKEND", "rust-pyo3")?;
    Ok(())
}

