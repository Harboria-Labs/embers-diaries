use super::*;
use std::collections::{BTreeSet, HashMap};
const FIELDS: [&str; 4] = ["subject", "primary_context", "content", "tags"];
pub fn limits(v: &Value) -> Result<Value> {
    let mut b = json!({"subjects":5,"contexts":10,"memories_per_context":3,"records":20,"candidates":100,"relationships":24,"preview_chars":240,"response_bytes":32768});
    let caps = json!({"subjects":20,"contexts":40,"memories_per_context":10,"records":100,"candidates":500,"relationships":100,"preview_chars":1000,"response_bytes":131072});
    if !v.is_null() {
        for (k, n) in v.as_object().ok_or("limits must be object")? {
            let cap = caps[k].as_u64().ok_or("unknown limit")?;
            let n = n.as_u64().ok_or("limits must be integers")?;
            if n == 0 || n > cap {
                return Err(format!("invalid {k}"));
            }
            b[k] = json!(n);
        }
    }
    if b["response_bytes"].as_u64().unwrap() < 4096 {
        return Err("response_bytes must be at least 4096".into());
    }
    Ok(b)
}
fn label(v: &Value) -> bool {
    v.is_null() || v.as_str().is_some_and(|s| s.len() <= 256)
}
pub fn orient(v: Value) -> Result<Value> {
    let clues = v["clues"].as_str().ok_or("clues must be text")?;
    if clues.trim().is_empty() || clues.len() > 2048 {
        return Err("invalid clues".into());
    }
    let ns = v["namespace"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or("namespace is required")?;
    let hints = v
        .get("hints")
        .filter(|x| !x.is_null())
        .cloned()
        .unwrap_or(json!({}));
    keys(&hints, &["subject", "primary_context"], false)?;
    if hints.as_object().unwrap().values().any(|v| !label(v)) {
        return Err("invalid hints".into());
    }
    let enabled = v
        .get("signals")
        .filter(|x| !x.is_null())
        .cloned()
        .unwrap_or(json!([
            "subject",
            "primary_context",
            "content",
            "tags",
            "relations"
        ]));
    let signals = enabled.as_array().ok_or("signals must be array")?;
    let set: BTreeSet<_> = signals.iter().filter_map(Value::as_str).collect();
    if set.len() != signals.len()
        || set.iter().any(|s| !FIELDS.contains(s) && *s != "relations")
        || !FIELDS.iter().any(|f| set.contains(f))
    {
        return Err(
            "signals must be distinct supported names with at least one lexical field".into(),
        );
    }
    let budget = limits(&v["limits"])?;
    if v["prepare"] == true {
        return Ok(json!({"bounds":budget,"hints":hints,"signals":enabled}));
    }
    let cap = |k: &str| budget[k].as_u64().unwrap() as usize;
    let records = v["records"].as_object().ok_or("records required")?;
    let shortlisted = v["shortlisted"].as_array().ok_or("shortlisted required")?;
    let words: BTreeSet<_> = v["words"]
        .as_array()
        .ok_or("words required")?
        .iter()
        .filter_map(Value::as_str)
        .collect();
    let mut rows: HashMap<String, Value> = HashMap::new();
    let mut ranks: HashMap<String, (i64, i64, i64, String)> = HashMap::new();
    let mut skipped = 0;
    let mut load = |rid: &str| -> Option<(Value, (i64, i64, i64, String))> {
        let rec = records.get(rid)?;
        if rec["namespace"] != ns
            || rec["retrieval_candidate"] != true
            || !["node", "document"].contains(&rec["record_type"].as_str().unwrap_or(""))
            || rec["current"] != true
        {
            return None;
        }
        let subject = &rec["subject"];
        let context = &rec["primary_context"];
        if !label(subject) || !label(context) || rid.len() > 256 {
            skipped += 1;
            return None;
        }
        let mut matched = serde_json::Map::new();
        let mut unique = BTreeSet::new();
        let mut hint_count = 0;
        for field in FIELDS {
            if !set.contains(field) {
                continue;
            }
            let tokens: BTreeSet<_> = rec["tokens"][field]
                .as_array()?
                .iter()
                .filter_map(Value::as_str)
                .collect();
            let mt: Vec<_> = words.intersection(&tokens).copied().collect();
            let hints: BTreeSet<_> = v["hint_words"][field]
                .as_array()
                .map(|a| a.iter().filter_map(Value::as_str).collect())
                .unwrap_or_default();
            let ht: Vec<_> = hints.intersection(&tokens).copied().collect();
            if !mt.is_empty() || !ht.is_empty() {
                unique.extend(mt.iter().copied());
                hint_count += ht.len();
                matched.insert(field.into(), json!({"clue_terms":mt,"hint_terms":ht}));
            }
        }
        let rank = (
            -(unique.len() as i64),
            -(hint_count as i64),
            -(matched.len() as i64),
            rid.to_string(),
        );
        let content = rec["content"].as_str().unwrap_or("");
        let row = json!({"id":rid,"subject":subject,"primary_context":context,"preview":content.chars().take(cap("preview_chars")).collect::<String>(),"preview_truncated":content.chars().count()>cap("preview_chars"),"signals":matched,"relationships":[],"provenance":rec["provenance"],"evidence":rec["evidence"],"ranking":{"unique_clue_terms":unique.len(),"hint_terms":hint_count,"matching_fields":matched.len()}});
        Some((row, rank))
    };
    for id in shortlisted.iter().take(cap("candidates")) {
        let rid = id.as_str().ok_or("candidate ID required")?;
        if let Some((row, rank)) = load(rid) {
            if !row["signals"].as_object().unwrap().is_empty() {
                rows.insert(rid.into(), row);
                ranks.insert(rid.into(), rank);
            }
        }
    }
    let mut seeds: Vec<_> = rows.keys().cloned().collect();
    seeds.sort_by_key(|k| ranks[k].clone());
    if v["plan"] == true {
        return Ok(json!({"seeds":seeds}));
    }
    let mut examined = 0;
    if set.contains("relations") {
        for source in &seeds {
            if let Some(edges) = v["edges"][source].as_array() {
                for edge in edges {
                    if examined >= cap("relationships") {
                        break;
                    }
                    examined += 1;
                    let id = edge["target"].as_str().ok_or("edge target required")?;
                    if !rows.contains_key(id) {
                        if rows.len() >= cap("candidates") {
                            continue;
                        }
                        if let Some((row, rank)) = load(id) {
                            rows.insert(id.into(), row);
                            ranks.insert(id.into(), rank);
                        } else {
                            continue;
                        }
                    }
                    rows.get_mut(id).unwrap()["relationships"].as_array_mut().unwrap().push(json!({"from_id":source,"to_id":id,"type":edge["edge_type"].as_str().unwrap_or("").chars().take(128).collect::<String>(),"label":edge["label"].as_str().unwrap_or("").chars().take(128).collect::<String>()}));
                }
            }
        }
    }
    let mut ordered: Vec<_> = rows.keys().cloned().collect();
    ordered.sort_by_key(|k| ranks[k].clone());
    let mut territory: Vec<Value> = vec![];
    let mut returned = vec![];
    let mut contexts = 0;
    for id in &ordered {
        let row = &rows[id];
        let si = territory
            .iter()
            .position(|s| s["subject"] == row["subject"]);
        if si.is_none() && territory.len() >= cap("subjects") {
            continue;
        }
        let ci = si.and_then(|i| {
            territory[i]["contexts"]
                .as_array()
                .unwrap()
                .iter()
                .position(|c| c["primary_context"] == row["primary_context"])
        });
        if ci.is_none() && contexts >= cap("contexts") {
            continue;
        }
        if returned.len() >= cap("records") {
            break;
        }
        if let (Some(i), Some(j)) = (si, ci) {
            if territory[i]["contexts"][j]["memories"]
                .as_array()
                .unwrap()
                .len()
                >= cap("memories_per_context")
            {
                continue;
            }
        }
        let i = si.unwrap_or_else(|| {
            territory.push(json!({"subject":row["subject"],"contexts":[]}));
            territory.len() - 1
        });
        let j = ci.unwrap_or_else(|| {
            let cs = territory[i]["contexts"].as_array_mut().unwrap();
            cs.push(json!({"primary_context":row["primary_context"],"memories":[]}));
            contexts += 1;
            cs.len() - 1
        });
        territory[i]["contexts"][j]["memories"]
            .as_array_mut()
            .unwrap()
            .push(row.clone());
        returned.push(json!(id));
    }
    let mut out = json!({"method":"orientation-lexical-v1","clues":clues,"hints":hints,"namespace":ns,"enabled_signals":enabled,"bounds":budget,"coverage":"bounded BM25 shortlist, not exhaustive","candidate_ids":ordered,"territory":territory,"returned_ids":returned,"diagnostics":{"shortlist_count":shortlisted.len().min(cap("candidates")),"shortlist_capped":v["shortlist_capped"],"edges_examined":examined,"skipped_oversize_labels":skipped,"candidate_ids_omitted":0,"byte_budget_trimmed":false},"decision":"agent_selects_context","response_bytes":0});
    loop {
        for _ in 0..8 {
            let size = out.to_string().len();
            if out["response_bytes"] == json!(size) {
                break;
            }
            out["response_bytes"] = json!(size);
        }
        if out.to_string().len() <= cap("response_bytes") {
            break;
        }
        out["diagnostics"]["byte_budget_trimmed"] = json!(true);
        if out["candidate_ids"].as_array_mut().unwrap().pop().is_some() {
            out["diagnostics"]["candidate_ids_omitted"] = json!(
                out["diagnostics"]["candidate_ids_omitted"]
                    .as_u64()
                    .unwrap()
                    + 1
            );
        } else if let Some(s) = out["territory"].as_array_mut().unwrap().last_mut() {
            let cs = s["contexts"].as_array_mut().unwrap();
            let c = cs.last_mut().unwrap();
            let row = c["memories"].as_array_mut().unwrap().pop().unwrap();
            if c["memories"].as_array().unwrap().is_empty() {
                cs.pop();
            }
            let empty = cs.is_empty();
            if empty {
                out["territory"].as_array_mut().unwrap().pop();
            }
            out["returned_ids"]
                .as_array_mut()
                .unwrap()
                .retain(|id| *id != row["id"]);
        } else {
            return Err("echoed request exceeds response budget".into());
        }
    }
    Ok(out)
}
