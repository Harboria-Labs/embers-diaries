use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine;
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use pyo3::IntoPyObjectExt;
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, Cursor, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static TEMP_FILE_COUNTER: AtomicU64 = AtomicU64::new(0);

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

#[derive(Serialize)]
struct PendingWalEntry<'a> {
    wal_id: &'a str,
    operation: &'a str,
    record_id: &'a str,
    data: Value,
    status: &'static str,
    timestamp: &'a str,
}

#[derive(Serialize)]
struct CommittedWalEntry<'a> {
    wal_id: &'a str,
    status: &'static str,
    timestamp: &'a str,
}

#[derive(Serialize)]
struct SupersessionEntry<'a> {
    old_id: &'a str,
    new_id: &'a str,
    timestamp: &'a str,
}

fn append_json<T: Serialize>(path: &Path, value: &T) -> PyResult<()> {
    let line = serde_json::to_vec(value)
        .map_err(|error| PyValueError::new_err(format!("invalid WAL entry: {error}")))?;
    durable_append(path, &line)?;
    Ok(())
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

fn temporary_sibling(path: &Path) -> io::Result<PathBuf> {
    let parent = path.parent().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, "path has no parent directory")
    })?;
    let name = path
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no file name"))?;
    let counter = TEMP_FILE_COUNTER.fetch_add(1, Ordering::Relaxed);
    Ok(parent.join(format!(
        ".{}.{}.{}.tmp",
        name.to_string_lossy(),
        std::process::id(),
        counter,
    )))
}

fn write_synced_temporary_file(path: &Path, data: &[u8]) -> io::Result<PathBuf> {
    let temp = temporary_sibling(path)?;
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temp)?;
    if let Err(error) = (|| {
        file.write_all(data)?;
        file.flush()?;
        file.sync_all()
    })() {
        let _ = fs::remove_file(&temp);
        return Err(error);
    }
    drop(file);
    Ok(temp)
}

fn atomic_write_new_file(path: &Path, data: &[u8]) -> io::Result<bool> {
    if path.exists() {
        return Ok(false);
    }
    let temp = write_synced_temporary_file(path, data)?;

    match fs::hard_link(&temp, path) {
        Ok(()) => {
            let _ = fs::remove_file(&temp);
            Ok(true)
        }
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
            let _ = fs::remove_file(&temp);
            Ok(false)
        }
        Err(error) => {
            let _ = fs::remove_file(&temp);
            Err(error)
        }
    }
}

#[cfg(windows)]
fn replace_path(source: &Path, destination: &Path) -> io::Result<()> {
    use std::iter::once;
    use std::os::windows::ffi::OsStrExt;

    const MOVEFILE_REPLACE_EXISTING: u32 = 0x1;
    const MOVEFILE_WRITE_THROUGH: u32 = 0x8;

    #[link(name = "Kernel32")]
    extern "system" {
        fn MoveFileExW(
            existing_file_name: *const u16,
            new_file_name: *const u16,
            flags: u32,
        ) -> i32;
    }

    let source_wide: Vec<u16> = source.as_os_str().encode_wide().chain(once(0)).collect();
    let destination_wide: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(once(0))
        .collect();
    let result = unsafe {
        MoveFileExW(
            source_wide.as_ptr(),
            destination_wide.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(windows))]
fn replace_path(source: &Path, destination: &Path) -> io::Result<()> {
    fs::rename(source, destination)
}

fn atomic_replace_file(path: &Path, data: &[u8]) -> io::Result<()> {
    let temp = write_synced_temporary_file(path, data)?;
    if let Err(error) = replace_path(&temp, path) {
        let _ = fs::remove_file(&temp);
        return Err(error);
    }
    Ok(())
}

#[pyclass]
struct StoreFileLock {
    file: Option<File>,
}

impl Drop for StoreFileLock {
    fn drop(&mut self) {
        if let Some(file) = self.file.take() {
            let _ = file.unlock();
        }
    }
}

#[pymethods]
impl StoreFileLock {
    fn release(&mut self) -> PyResult<()> {
        if let Some(file) = self.file.take() {
            file.unlock()?;
        }
        Ok(())
    }
}

#[pyfunction]
fn acquire_store_lock(py: Python<'_>, path: &str) -> PyResult<StoreFileLock> {
    let file = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .truncate(false)
        .open(path)?;
    py.allow_threads(|| file.lock())?;
    Ok(StoreFileLock { file: Some(file) })
}

#[pyfunction]
fn atomic_write_new(path: &str, data: &[u8]) -> PyResult<bool> {
    Ok(atomic_write_new_file(Path::new(path), data)?)
}

#[pyfunction]
fn atomic_replace(path: &str, data: &[u8]) -> PyResult<()> {
    atomic_replace_file(Path::new(path), data)?;
    Ok(())
}

fn supersession_path(root: &Path, record_id: &str) -> PathBuf {
    root.join("supersessions")
        .join(format!("{record_id}.superseded"))
}

fn read_superseded_by(root: &Path, record_id: &str) -> io::Result<Option<String>> {
    let path = supersession_path(root, record_id);
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let entry: Value = serde_json::from_slice(&bytes)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    Ok(entry
        .get("new_id")
        .and_then(Value::as_str)
        .map(str::to_owned))
}

fn record_header_from_msgpack(bytes: &[u8]) -> Option<(Option<String>, i64)> {
    let value = rmpv::decode::read_value(&mut Cursor::new(bytes)).ok()?;
    let rmpv::Value::Map(fields) = value else {
        return None;
    };
    let mut content_hash = None;
    let mut version = 1;
    for (key, value) in fields {
        match key.as_str() {
            Some("content_hash") => {
                content_hash = value.as_str().map(str::to_owned);
            }
            Some("version") => {
                version = value.as_i64().unwrap_or(1);
            }
            _ => {}
        }
    }
    Some((content_hash, version))
}

fn record_header_from_json(bytes: &[u8]) -> Option<(Option<String>, i64)> {
    let value: Value = serde_json::from_slice(bytes).ok()?;
    Some((
        value
            .get("content_hash")
            .and_then(Value::as_str)
            .map(str::to_owned),
        value.get("version").and_then(Value::as_i64).unwrap_or(1),
    ))
}

fn read_record_header(root: &Path, record_id: &str) -> io::Result<(Option<String>, i64)> {
    let path = root.join("records").join(format!("{record_id}.ember"));
    let bytes = fs::read(path)?;
    record_header_from_msgpack(&bytes)
        .or_else(|| record_header_from_json(&bytes))
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "invalid Ember record encoding"))
}

fn python_to_msgpack(value: &Bound<'_, PyAny>) -> PyResult<rmpv::Value> {
    if value.is_none() {
        return Ok(rmpv::Value::Nil);
    }
    if value.is_instance_of::<PyBool>() {
        return Ok(rmpv::Value::Boolean(value.extract()?));
    }
    if let Ok(bytes) = value.downcast::<PyBytes>() {
        return Ok(rmpv::Value::Binary(bytes.as_bytes().to_vec()));
    }
    if let Ok(text) = value.downcast::<PyString>() {
        return Ok(rmpv::Value::String(text.extract::<String>()?.into()));
    }
    if value.is_instance_of::<PyInt>() {
        if let Ok(integer) = value.extract::<i64>() {
            return Ok(rmpv::Value::Integer(integer.into()));
        }
        if let Ok(integer) = value.extract::<u64>() {
            return Ok(rmpv::Value::Integer(integer.into()));
        }
        return Err(PyValueError::new_err(
            "integer is outside the MessagePack signed/unsigned 64-bit range",
        ));
    }
    if value.is_instance_of::<PyFloat>() {
        return Ok(rmpv::Value::F64(value.extract()?));
    }
    if let Ok(list) = value.downcast::<PyList>() {
        return list
            .iter()
            .map(|item| python_to_msgpack(&item))
            .collect::<PyResult<Vec<_>>>()
            .map(rmpv::Value::Array);
    }
    if let Ok(tuple) = value.downcast::<PyTuple>() {
        return tuple
            .iter()
            .map(|item| python_to_msgpack(&item))
            .collect::<PyResult<Vec<_>>>()
            .map(rmpv::Value::Array);
    }
    if let Ok(dictionary) = value.downcast::<PyDict>() {
        let mut entries = Vec::with_capacity(dictionary.len());
        for (key, item) in dictionary.iter() {
            entries.push((python_to_msgpack(&key)?, python_to_msgpack(&item)?));
        }
        return Ok(rmpv::Value::Map(entries));
    }
    Err(PyTypeError::new_err(format!(
        "unsupported MessagePack value: {}",
        value.get_type().name()?
    )))
}

fn python_to_canonical_json(value: &Bound<'_, PyAny>) -> PyResult<Value> {
    if value.is_none() {
        return Ok(Value::Null);
    }
    if value.is_instance_of::<PyBool>() {
        return Ok(Value::Bool(value.extract()?));
    }
    if let Ok(bytes) = value.downcast::<PyBytes>() {
        let mut wrapped = serde_json::Map::new();
        wrapped.insert(
            "$bytes".to_owned(),
            Value::String(BASE64_STANDARD.encode(bytes.as_bytes())),
        );
        return Ok(Value::Object(wrapped));
    }
    if let Ok(text) = value.downcast::<PyString>() {
        return Ok(Value::String(text.extract()?));
    }
    if value.is_instance_of::<PyInt>() {
        if let Ok(integer) = value.extract::<i64>() {
            return Ok(Value::Number(integer.into()));
        }
        if let Ok(integer) = value.extract::<u64>() {
            return Ok(Value::Number(integer.into()));
        }
        return Err(PyValueError::new_err(
            "integer is outside the canonical signed/unsigned 64-bit range",
        ));
    }
    if value.is_instance_of::<PyFloat>() {
        let number = serde_json::Number::from_f64(value.extract()?).ok_or_else(|| {
            PyValueError::new_err("Canonical record content cannot contain NaN or infinity")
        })?;
        return Ok(Value::Number(number));
    }
    if let Ok(list) = value.downcast::<PyList>() {
        return list
            .iter()
            .map(|item| python_to_canonical_json(&item))
            .collect::<PyResult<Vec<_>>>()
            .map(Value::Array);
    }
    if let Ok(tuple) = value.downcast::<PyTuple>() {
        return tuple
            .iter()
            .map(|item| python_to_canonical_json(&item))
            .collect::<PyResult<Vec<_>>>()
            .map(Value::Array);
    }
    if let Ok(dictionary) = value.downcast::<PyDict>() {
        let mut normalized = serde_json::Map::new();
        for (key, item) in dictionary.iter() {
            let key = key.extract::<String>().map_err(|_| {
                PyTypeError::new_err("Canonical record dictionaries require string keys")
            })?;
            normalized.insert(key, python_to_canonical_json(&item)?);
        }
        return Ok(Value::Object(normalized));
    }
    Err(PyTypeError::new_err(format!(
        "Unsupported canonical record value: {}",
        value.get_type().name()?
    )))
}

#[pyfunction]
fn canonical_record_bytes(payload: &Bound<'_, PyAny>) -> PyResult<Vec<u8>> {
    let normalized = python_to_canonical_json(payload)?;
    serde_json::to_vec(&normalized)
        .map_err(|error| PyValueError::new_err(format!("canonical encoding failed: {error}")))
}

fn json_to_msgpack(value: Value) -> rmpv::Value {
    match value {
        Value::Null => rmpv::Value::Nil,
        Value::Bool(value) => rmpv::Value::Boolean(value),
        Value::Number(value) => {
            if let Some(integer) = value.as_i64() {
                rmpv::Value::Integer(integer.into())
            } else if let Some(integer) = value.as_u64() {
                rmpv::Value::Integer(integer.into())
            } else {
                rmpv::Value::F64(value.as_f64().unwrap_or_default())
            }
        }
        Value::String(value) => rmpv::Value::String(value.into()),
        Value::Array(values) => {
            rmpv::Value::Array(values.into_iter().map(json_to_msgpack).collect())
        }
        Value::Object(values) => rmpv::Value::Map(
            values
                .into_iter()
                .map(|(key, value)| (key.into(), json_to_msgpack(value)))
                .collect(),
        ),
    }
}

fn msgpack_to_python(py: Python<'_>, value: rmpv::Value) -> PyResult<Py<PyAny>> {
    match value {
        rmpv::Value::Nil => Ok(py.None()),
        rmpv::Value::Boolean(value) => value.into_py_any(py),
        rmpv::Value::Integer(value) => {
            if let Some(integer) = value.as_i64() {
                integer.into_py_any(py)
            } else if let Some(integer) = value.as_u64() {
                integer.into_py_any(py)
            } else {
                Err(PyValueError::new_err("invalid MessagePack integer"))
            }
        }
        rmpv::Value::F32(value) => value.into_py_any(py),
        rmpv::Value::F64(value) => value.into_py_any(py),
        rmpv::Value::String(value) => value
            .as_str()
            .ok_or_else(|| PyValueError::new_err("invalid UTF-8 MessagePack string"))?
            .into_py_any(py),
        rmpv::Value::Binary(value) => Ok(PyBytes::new(py, &value).into_any().unbind()),
        rmpv::Value::Array(values) => {
            let values = values
                .into_iter()
                .map(|value| msgpack_to_python(py, value))
                .collect::<PyResult<Vec<_>>>()?;
            Ok(PyList::new(py, values)?.into_any().unbind())
        }
        rmpv::Value::Map(entries) => {
            let dictionary = PyDict::new(py);
            for (key, value) in entries {
                dictionary.set_item(msgpack_to_python(py, key)?, msgpack_to_python(py, value)?)?;
            }
            Ok(dictionary.into_any().unbind())
        }
        rmpv::Value::Ext(_, _) => Err(PyValueError::new_err(
            "MessagePack extension values are not supported by Ember records",
        )),
    }
}

#[pyfunction]
fn encode_record(record: &Bound<'_, PyAny>) -> PyResult<Vec<u8>> {
    let value = python_to_msgpack(record)?;
    let mut bytes = Vec::new();
    rmpv::encode::write_value(&mut bytes, &value)
        .map_err(|error| PyValueError::new_err(format!("record encoding failed: {error}")))?;
    Ok(bytes)
}

#[pyfunction]
fn decode_record(py: Python<'_>, raw: &[u8]) -> PyResult<Py<PyAny>> {
    let mut cursor = Cursor::new(raw);
    let value = match rmpv::decode::read_value(&mut cursor) {
        Ok(value) if cursor.position() as usize == raw.len() => value,
        _ => {
            let json = serde_json::from_slice(raw).map_err(|error| {
                PyValueError::new_err(format!("invalid MessagePack/JSON record: {error}"))
            })?;
            json_to_msgpack(json)
        }
    };
    msgpack_to_python(py, value)
}

#[pyfunction]
fn write_supersession(root: &str, old_id: &str, new_id: &str, timestamp: &str) -> PyResult<()> {
    let root = Path::new(root);
    fs::create_dir_all(root.join("supersessions"))?;
    let line = serde_json::to_vec(&SupersessionEntry {
        old_id,
        new_id,
        timestamp,
    })
    .map_err(|error| PyValueError::new_err(format!("invalid supersession: {error}")))?;
    atomic_replace_file(&supersession_path(root, old_id), &line)?;
    Ok(())
}

#[pyfunction]
fn get_superseded_by(root: &str, record_id: &str) -> PyResult<Option<String>> {
    Ok(read_superseded_by(Path::new(root), record_id)?)
}

#[pyfunction]
fn get_supersession_chain(root: &str, record_id: &str) -> PyResult<Vec<String>> {
    let root = Path::new(root);
    let mut chain = vec![record_id.to_owned()];
    let mut current = record_id.to_owned();
    let mut seen = HashSet::new();
    loop {
        if !seen.insert(current.clone()) {
            break;
        }
        let Some(next_id) = read_superseded_by(root, &current)? else {
            break;
        };
        chain.push(next_id.clone());
        current = next_id;
    }
    Ok(chain)
}

#[pyfunction]
fn resolve_cas_head(
    root: &str,
    record_id: &str,
    expected_hash: &str,
) -> PyResult<(String, Option<String>, i64, bool)> {
    let chain = get_supersession_chain(root, record_id)?;
    let head_id = chain
        .last()
        .cloned()
        .unwrap_or_else(|| record_id.to_owned());
    let (actual_hash, version) = read_record_header(Path::new(root), &head_id)?;
    let matches = actual_hash.as_deref() == Some(expected_hash);
    Ok((head_id, actual_hash, version, matches))
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
fn append_wal_pending(
    path: &str,
    wal_id: &str,
    operation: &str,
    record_id: &str,
    data_json: &[u8],
    timestamp: &str,
) -> PyResult<()> {
    let data = serde_json::from_slice(data_json)
        .map_err(|error| PyValueError::new_err(format!("invalid WAL data: {error}")))?;
    append_json(
        Path::new(path),
        &PendingWalEntry {
            wal_id,
            operation,
            record_id,
            data,
            status: "PENDING",
            timestamp,
        },
    )
}

#[pyfunction]
fn append_wal_commit(path: &str, wal_id: &str, timestamp: &str) -> PyResult<()> {
    append_json(
        Path::new(path),
        &CommittedWalEntry {
            wal_id,
            status: "COMMITTED",
            timestamp,
        },
    )
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
    module.add_function(wrap_pyfunction!(append_wal_pending, module)?)?;
    module.add_function(wrap_pyfunction!(append_wal_commit, module)?)?;
    module.add_function(wrap_pyfunction!(recover_wal_lines, module)?)?;
    module.add_function(wrap_pyfunction!(checkpoint_wal, module)?)?;
    module.add_function(wrap_pyfunction!(acquire_store_lock, module)?)?;
    module.add_function(wrap_pyfunction!(atomic_write_new, module)?)?;
    module.add_function(wrap_pyfunction!(atomic_replace, module)?)?;
    module.add_function(wrap_pyfunction!(write_supersession, module)?)?;
    module.add_function(wrap_pyfunction!(get_superseded_by, module)?)?;
    module.add_function(wrap_pyfunction!(get_supersession_chain, module)?)?;
    module.add_function(wrap_pyfunction!(resolve_cas_head, module)?)?;
    module.add_function(wrap_pyfunction!(encode_record, module)?)?;
    module.add_function(wrap_pyfunction!(decode_record, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_record_bytes, module)?)?;
    module.add_class::<StoreFileLock>()?;
    module.add("BACKEND", "rust-pyo3")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        append_json, atomic_replace_file, atomic_write_new_file, compact_wal, durable_append,
        pending_wal_lines, CommittedWalEntry, PendingWalEntry,
    };
    use serde_json::json;
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

    #[test]
    fn rust_encodes_pending_and_commit_envelopes() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock before epoch")
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "ember-native-wal-encoding-{}-{nonce}.jsonl",
            std::process::id(),
        ));

        append_json(
            &path,
            &PendingWalEntry {
                wal_id: "wal-1",
                operation: "write",
                record_id: "record-1",
                data: json!({"value": 7}),
                status: "PENDING",
                timestamp: "2026-09-15T00:00:00+00:00",
            },
        )
        .expect("append pending entry");
        append_json(
            &path,
            &CommittedWalEntry {
                wal_id: "wal-1",
                status: "COMMITTED",
                timestamp: "2026-09-15T00:00:01+00:00",
            },
        )
        .expect("append commit entry");

        let lines = fs::read_to_string(&path).expect("read encoded WAL");
        assert_eq!(
            lines,
            concat!(
                "{\"wal_id\":\"wal-1\",\"operation\":\"write\",\"record_id\":\"record-1\",",
                "\"data\":{\"value\":7},\"status\":\"PENDING\",",
                "\"timestamp\":\"2026-09-15T00:00:00+00:00\"}\n",
                "{\"wal_id\":\"wal-1\",\"status\":\"COMMITTED\",",
                "\"timestamp\":\"2026-09-15T00:00:01+00:00\"}\n",
            )
        );
        fs::remove_file(path).expect("remove encoded WAL");
    }

    #[test]
    fn atomic_new_file_never_overwrites_an_existing_record() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock before epoch")
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "ember-native-record-{}-{nonce}.ember",
            std::process::id(),
        ));

        assert!(atomic_write_new_file(&path, b"first").expect("write new record"));
        assert!(!atomic_write_new_file(&path, b"second").expect("refuse overwrite"));
        assert_eq!(fs::read(&path).expect("read record"), b"first");
        fs::remove_file(path).expect("remove record");
    }

    #[test]
    fn atomic_replace_publishes_complete_new_contents() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock before epoch")
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "ember-native-sidecar-{}-{nonce}.json",
            std::process::id(),
        ));
        fs::write(&path, b"old").expect("write old sidecar");

        atomic_replace_file(&path, b"complete-new-value").expect("replace sidecar");

        assert_eq!(
            fs::read(&path).expect("read sidecar"),
            b"complete-new-value"
        );
        fs::remove_file(path).expect("remove sidecar");
    }
}
