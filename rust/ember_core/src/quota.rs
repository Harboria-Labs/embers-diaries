//! Logical-byte admission for a managed store root, not a filesystem/RAM quota.
use std::cell::RefCell;
use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};

thread_local! { static HELD: RefCell<HashSet<PathBuf>> = RefCell::new(HashSet::new()); }

pub struct Guard { root: Option<PathBuf>, _file: Option<File> }
impl Drop for Guard {
    fn drop(&mut self) {
        if let Some(root) = &self.root {
            HELD.with(|held| { held.borrow_mut().remove(root); });
        }
    }
}

pub fn usage(root: &Path) -> io::Result<u64> {
    let mut size = 0u64;
    if !root.exists() { return Ok(0); }
    for entry in fs::read_dir(root)? {
        let path = entry?.path();
        let meta = fs::symlink_metadata(&path)?;
        if meta.file_type().is_symlink() {
            return Err(io::Error::new(io::ErrorKind::InvalidInput,
                "quota-managed stores cannot contain symlinks"));
        }
        let value = if meta.is_dir() { usage(&path)? } else { meta.len() };
        size = size.checked_add(value).ok_or_else(|| io::Error::other("byte count overflow"))?;
    }
    Ok(size)
}

fn policy(root: &Path) -> PathBuf { root.join("meta").join("storage-limits.json") }

pub fn limit(root: &Path) -> io::Result<Option<u64>> {
    let path = policy(root);
    if !path.exists() { return Ok(None); }
    let data: serde_json::Value = serde_json::from_slice(&fs::read(path)?)
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
    data.get("max_total_bytes").and_then(|v| v.as_u64()).filter(|v| *v > 0)
        .map(Some).ok_or_else(|| io::Error::other("invalid total-byte policy"))
}

fn locked(root: &Path) -> io::Result<Guard> {
    let root = fs::canonicalize(root)?;
    if HELD.with(|held| held.borrow().contains(&root)) {
        return Ok(Guard { root: None, _file: None });
    }
    let file = OpenOptions::new().create(true).truncate(false).read(true).write(true)
        .open(root.join(".ember.capacity.lock"))?;
    file.lock()?;
    HELD.with(|held| { held.borrow_mut().insert(root.clone()); });
    Ok(Guard { root: Some(root), _file: Some(file) })
}

pub fn commit_reserve(wal_id: &str) -> u64 {
    serde_json::to_vec(wal_id).map_or(0, |v| v.len() as u64) + 256
}

fn pending_reserve(root: &Path, line: &[u8]) -> io::Result<u64> {
    let data: serde_json::Value = serde_json::from_slice(line)
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
    if data.get("operation").and_then(|v| v.as_str()) != Some("write") { return Ok(0); }
    let id = data.get("record_id").and_then(|v| v.as_str())
        .ok_or_else(|| io::Error::other("pending WAL lacks record identity"))?;
    let wal_id = data.get("wal_id").and_then(|v| v.as_str())
        .ok_or_else(|| io::Error::other("pending WAL lacks transaction identity"))?;
    let mut reserved = commit_reserve(wal_id);
    if !root.join("records").join(format!("{id}.ember")).exists() {
        let mut bytes = Vec::new();
        rmpv::encode::write_value(&mut bytes, &super::json_to_msgpack(data["data"].clone()))
            .map_err(|e| io::Error::other(e.to_string()))?;
        reserved = reserved.checked_add(bytes.len() as u64)
            .ok_or_else(|| io::Error::other("reservation overflow"))?;
    }
    Ok(reserved)
}

pub fn recovery_reserve(root: &Path) -> io::Result<u64> {
    let mut reserved = 0u64;
    for line in super::pending_wal_lines(&root.join("wal.jsonl"))? {
        reserved = reserved.checked_add(pending_reserve(root, &line)?)
            .ok_or_else(|| io::Error::other("reservation overflow"))?;
    }
    Ok(reserved)
}

pub fn admit_append(path: &Path, line: &[u8]) -> io::Result<Guard> {
    let growth = (line.len() as u64).checked_add(1)
        .ok_or_else(|| io::Error::other("byte count overflow"))?;
    if let Some(root) = path.ancestors().skip(1).find(|p| policy(p).exists()) {
        let canonical = fs::canonicalize(root)?;
        let nested = HELD.with(|held| held.borrow().contains(&canonical));
        if !nested {
            let data: serde_json::Value = serde_json::from_slice(line)
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
            if data.get("status").and_then(|v| v.as_str()) == Some("COMMITTED") {
                let guard = locked(root)?;
                let mut reserve = recovery_reserve(root)?;
                for pending in super::pending_wal_lines(&root.join("wal.jsonl"))? {
                    let item: serde_json::Value = serde_json::from_slice(&pending)?;
                    if item.get("wal_id") == data.get("wal_id") {
                        // A commit releases its reservation only when its record exists.
                        let id = item.get("record_id").and_then(|v| v.as_str()).unwrap_or("");
                        if !root.join("records").join(format!("{id}.ember")).exists() {
                            return Err(io::Error::other("cannot commit missing record"));
                        }
                        reserve = reserve.saturating_sub(pending_reserve(root, &pending)?);
                    }
                }
                let cap = limit(root)?.ok_or_else(|| io::Error::other("quota policy disappeared"))?;
                if usage(root)?.checked_add(reserve).and_then(|n| n.checked_add(growth)).is_none_or(|n| n > cap) {
                    return Err(io::Error::other("storage.max_total_bytes exceeded by WAL commit"));
                }
                return Ok(guard);
            }
            if data.get("status").and_then(|v| v.as_str()) == Some("PENDING") {
                return admit(path, growth.checked_add(pending_reserve(root, line)?)
                    .ok_or_else(|| io::Error::other("reservation overflow"))?);
            }
        }
    }
    admit(path, growth)
}

pub fn admit(path: &Path, growth: u64) -> io::Result<Guard> {
    let Some(root) = path.ancestors().skip(1).find(|p| policy(p).exists()) else {
        return Ok(Guard { root: None, _file: None });
    };
    let guard = locked(root)?;
    let recovery = if guard.root.is_some() { recovery_reserve(root)? } else { 0 };
    let cap = limit(root)?.ok_or_else(|| io::Error::other("quota policy disappeared"))?;
    let attempted = usage(root)?.checked_add(recovery).and_then(|n| n.checked_add(growth))
        .ok_or_else(|| io::Error::other("byte count overflow"))?;
    if attempted > cap {
        return Err(io::Error::other(format!(
            "storage.max_total_bytes exceeded: limit={cap}, attempted={attempted}")));
    }
    Ok(guard)
}

pub fn configure(root: &Path, cap: u64) -> io::Result<()> {
    if cap == 0 { return Err(io::Error::other("total byte cap must be positive")); }
    fs::create_dir_all(root.join("meta"))?;
    let _guard = locked(root)?;
    if let Some(existing) = limit(root)? {
        if existing != cap {
            return Err(io::Error::other("persisted total-byte cap differs; explicit migration required"));
        }
        return Ok(());
    }
    let data = serde_json::to_vec(&serde_json::json!({"max_total_bytes": cap}))?;
    if usage(root)?.checked_add(recovery_reserve(root)?).and_then(|n| n.checked_add(data.len() as u64)).is_none_or(|n| n > cap) {
        return Err(io::Error::other("total byte cap cannot fit initial policy"));
    }
    super::atomic_replace_file(&policy(root), &data)
}
