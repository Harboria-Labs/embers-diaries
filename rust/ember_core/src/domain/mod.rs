//! Versioned deterministic Ember domain. No clocks, filesystem, credentials or UI.
pub mod ella;
pub mod dynamics;
pub mod fur;
pub mod orientation;
pub mod pairing;
use serde_json::{json, Value};
pub type Result<T> = std::result::Result<T, String>;
pub const MODEL: &str = "ember-fur-activation-v1";
pub fn text<'a>(v: &'a Value, name: &str) -> Result<&'a str> {
    let s = v.as_str().ok_or_else(|| format!("{name} must be text"))?;
    if s.trim().is_empty() || s.len() > 512 {
        return Err(format!("{name} must be nonempty text of at most 512 bytes"));
    }
    Ok(s)
}
pub fn number(v: &Value, name: &str) -> Result<f64> {
    v.as_f64()
        .filter(|x| x.is_finite())
        .ok_or_else(|| format!("{name} must be finite"))
}
pub fn context(v: &Value) -> Result<Value> {
    if v.is_null() {
        return Ok(Value::Null);
    }
    let s = v.as_str().ok_or("context must be string or null")?;
    if s.trim().is_empty() {
        return Err("context must be nonblank".into());
    }
    Ok(v.clone())
}
pub fn keys(v: &Value, allowed: &[&str], exact: bool) -> Result<()> {
    let o = v.as_object().ok_or("object required")?;
    if o.keys().any(|k| !allowed.contains(&k.as_str())) || (exact && o.len() != allowed.len()) {
        return Err("unsupported or missing fields".into());
    }
    Ok(())
}
pub fn canonical(v: &Value) -> String {
    match v {
        Value::Object(m) => {
            let sorted: std::collections::BTreeMap<_, _> = m.iter().collect();
            format!(
                "{{{}}}",
                sorted
                    .iter()
                    .map(|(k, v)| format!("{}:{}", serde_json::to_string(k).unwrap(), canonical(v)))
                    .collect::<Vec<_>>()
                    .join(",")
            )
        }
        Value::Array(a) => format!(
            "[{}]",
            a.iter().map(canonical).collect::<Vec<_>>().join(",")
        ),
        _ => v.to_string(),
    }
}
pub fn call(op: &str, v: Value) -> Result<Value> {
    match op {
        "research_guard" => {
            text(&v["request_id"], "request_id")?;
            for e in v["events"].as_array().ok_or("events required")? {
                if e["actor"] == v["actor"] && e["request_id"] == v["request_id"] {
                    if e["fingerprint"] != v["fingerprint"] {
                        return Err("request_id reused with different input".into());
                    }
                    return Ok(json!({"retry":e}));
                }
            }
            Ok(json!({"retry":null}))
        }
        "ella_crossing" => Ok(json!({"confirmation_required": v["before"]["base_epistemic_verdict"]=="PROVISIONAL" && ["VERIFIED","DISFAVORED"].contains(&v["after"]["base_epistemic_verdict"].as_str().unwrap_or(""))})),
        "ella_policy_default" => Ok(ella::defaults()),
        "ella_policy" => ella::policy(v),
        "ella_assessment" => ella::assessment(v),
        "ella_state" => ella::state(v),
        "ella_reduce" => ella::reduce(v),
        "ella_project" => ella::project(&v),
        "pair_select" => pairing::select(&v),
        "context" => context(&v),
        "context_write" => {
            context(&v["supplied"])?;
            if let Some(stored) = v["data"].get("primary_context") {
                context(stored)?;
                if !v["supplied"].is_null() && *stored != v["supplied"] {
                    return Err("conflicting primary context".into());
                }
            }
            let mut data = v["data"].clone();
            if data.get("primary_context").is_none() && !v["supplied"].is_null() {
                data["primary_context"] = v["supplied"].clone();
            }
            Ok(data)
        }
        "fur_policy" => fur::policy(v),
        "fur_target" => fur::target(v),
        "fur_derive" => fur::derive(&v["experiences"], &v["policy"]),
        "fur_apply" => fur::apply(v),
        "fur_reduce" => fur::reduce(v),
        "config" => dynamics::validate_config(v),
        "config_default" => Ok(dynamics::defaults()),
        "activation_batch" => dynamics::batch(&v),
        "activation" => dynamics::step(&v),
        "admit" => dynamics::admit(&v),
        "orientation" => orientation::orient(v),
        "truth" => {
            let mut projection=if v["projection"].is_object(){v["projection"].clone()}else{ella::project(&json!({"policy":ella::defaults(),"evidence":[],"assessments":[],"revision":0}))?};
            if v["open_conflict"]==true {projection["public_epistemic_state"]=json!("DISPUTED");projection["conflict_overlay"]=json!("open");}
            projection["status"]=json!(projection["public_epistemic_state"].as_str().unwrap().to_lowercase());
            projection["source"]=json!("epistemic_ledger");
            projection["projection_version"]=json!("ella-v1");
            projection["legacy_status"]=v["data"].get("_status").or_else(||v["data"].get("verify_status")).cloned().unwrap_or(Value::Null);
            Ok(projection)
        }
        _ => Err("unknown domain operation".into()),
    }
}
