use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use sha2::{Digest, Sha256};
use std::fs::OpenOptions;
use std::io::{self, Write};
use std::path::Path;

#[pyfunction]
fn sha256_hex(canonical_bytes: &[u8]) -> String {
    let digest = Sha256::digest(canonical_bytes);
    format!("{digest:x}")
}

fn durable_append(path: &Path, line: &[u8]) -> io::Result<()> {
    let mut framed = Vec::with_capacity(line.len() + 1);
    framed.extend_from_slice(line);
    framed.push(b'\n');

    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    file.write_all(&framed)?;
    file.flush()?;
    file.sync_all()
}

#[pyfunction]
fn append_wal_line(path: &str, line: &[u8]) -> PyResult<()> {
    if line.iter().any(|byte| matches!(byte, b'\n' | b'\r')) {
        return Err(PyValueError::new_err(
            "WAL payload must be exactly one JSON line without CR/LF",
        ));
    }
    durable_append(Path::new(path), line)?;
    Ok(())
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(sha256_hex, module)?)?;
    module.add_function(wrap_pyfunction!(append_wal_line, module)?)?;
    module.add("BACKEND", "rust-pyo3")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::durable_append;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn durable_append_frames_each_payload_once() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock before epoch")
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "ember-native-wal-{}-{nonce}.jsonl",
            std::process::id(),
        ));

        durable_append(&path, br#"{"status":"PENDING"}"#).expect("append pending frame");
        durable_append(&path, br#"{"status":"COMMITTED"}"#).expect("append commit frame");

        let bytes = fs::read(&path).expect("read appended WAL");
        assert_eq!(
            bytes,
            b"{\"status\":\"PENDING\"}\n{\"status\":\"COMMITTED\"}\n",
        );
        fs::remove_file(path).expect("remove test WAL");
    }
}
