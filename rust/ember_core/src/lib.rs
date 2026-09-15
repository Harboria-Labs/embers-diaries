use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs::{File, OpenOptions};
use std::io::{self, BufRead, BufReader, Write};
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

fn trim_ascii_whitespace(mut bytes: &[u8]) -> &[u8] {
    while bytes.first().is_some_and(u8::is_ascii_whitespace) {
        bytes = &bytes[1..];
    }
    while bytes.last().is_some_and(u8::is_ascii_whitespace) {
        bytes = &bytes[..bytes.len() - 1];
    }
    bytes
}

fn pending_wal_lines(path: &Path) -> io::Result<Vec<Vec<u8>>> {
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error),
    };
    let mut reader = BufReader::new(file);
    let mut raw_line = Vec::new();
    let mut entries: Vec<(String, Vec<u8>)> = Vec::new();
    let mut entry_indexes: HashMap<String, usize> = HashMap::new();
    let mut committed: HashSet<String> = HashSet::new();

    loop {
        raw_line.clear();
        if reader.read_until(b'\n', &mut raw_line)? == 0 {
            break;
        }
        let line = trim_ascii_whitespace(&raw_line);
        if line.is_empty() {
            continue;
        }
        let Ok(Value::Object(entry)) = serde_json::from_slice::<Value>(line) else {
            continue;
        };
        let wal_id = entry
            .get("wal_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned();
        match entry.get("status").and_then(Value::as_str) {
            Some("COMMITTED") => {
                committed.insert(wal_id);
            }
            Some("PENDING") => {
                if let Some(index) = entry_indexes.get(&wal_id).copied() {
                    entries[index].1 = line.to_vec();
                } else {
                    entry_indexes.insert(wal_id.clone(), entries.len());
                    entries.push((wal_id, line.to_vec()));
                }
            }
            _ => {}
        }
    }

    Ok(entries
        .into_iter()
        .filter_map(|(wal_id, line)| (!committed.contains(&wal_id)).then_some(line))
        .collect())
}

fn compact_wal(path: &Path) -> io::Result<usize> {
    let pending = pending_wal_lines(path)?;
    let mut file = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(path)?;
    for line in &pending {
        file.write_all(line)?;
        file.write_all(b"\n")?;
    }
    file.flush()?;
    file.sync_all()?;
    Ok(pending.len())
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

#[pyfunction]
fn recover_wal_lines(py: Python<'_>, path: &str) -> PyResult<Vec<Py<PyBytes>>> {
    Ok(pending_wal_lines(Path::new(path))?
        .into_iter()
        .map(|line| PyBytes::new(py, &line).unbind())
        .collect())
}

#[pyfunction]
fn checkpoint_wal(path: &str) -> PyResult<usize> {
    Ok(compact_wal(Path::new(path))?)
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(sha256_hex, module)?)?;
    module.add_function(wrap_pyfunction!(append_wal_line, module)?)?;
    module.add_function(wrap_pyfunction!(recover_wal_lines, module)?)?;
    module.add_function(wrap_pyfunction!(checkpoint_wal, module)?)?;
    module.add("BACKEND", "rust-pyo3")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{compact_wal, durable_append, pending_wal_lines};
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

    #[test]
    fn recovery_and_checkpoint_preserve_only_uncommitted_frames() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock before epoch")
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "ember-native-wal-recovery-{}-{nonce}.jsonl",
            std::process::id(),
        ));
        fs::write(
            &path,
            concat!(
                "not-json\n",
                "{\"wal_id\":\"pending\",\"status\":\"PENDING\",\"data\":1}\n",
                "{\"wal_id\":\"done\",\"status\":\"PENDING\",\"data\":2}\n",
                "{\"wal_id\":\"done\",\"status\":\"COMMITTED\"}\n",
                "{\"wal_id\":\"pending\",\"status\":\"PENDING\",\"data\":3}\n",
            ),
        )
        .expect("write recovery WAL");

        let pending = pending_wal_lines(&path).expect("recover pending WAL");
        assert_eq!(pending.len(), 1);
        assert_eq!(
            pending[0],
            b"{\"wal_id\":\"pending\",\"status\":\"PENDING\",\"data\":3}"
        );

        assert_eq!(compact_wal(&path).expect("checkpoint WAL"), 1);
        assert_eq!(
            fs::read(&path).expect("read checkpointed WAL"),
            b"{\"wal_id\":\"pending\",\"status\":\"PENDING\",\"data\":3}\n"
        );
        fs::remove_file(path).expect("remove recovery WAL");
    }
}
