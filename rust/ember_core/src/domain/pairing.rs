//! Pairing Matrix V1: read-only selection over stored contextual outgoing edges.
use super::*;
use sha2::{Digest, Sha256};
pub fn select(v: &Value) -> Result<Value> {
    context(&v["context"])?;
    let neutral = number(&v["neutral"], "neutral")?;
    let direct = v["direct_ids"].as_array().ok_or("direct_ids required")?;
    let Some(primary) = direct.first() else { return Ok(Value::Null) };
    let states = v["states"].as_object().ok_or("states required")?;
    let mut best: Option<(f64, String, Value)> = None;
    for edge in v["edges"].as_array().ok_or("edges required")? {
        // Missing context is distinct from explicitly recorded unset context.
        if edge["metadata"].get("primary_context") != Some(&v["context"])
            || direct.contains(&edge["target"]) { continue; }
        let target = json!({"kind":"pair","memory_ids":[primary,edge["target"]],"relation":edge["edge_type"]});
        let key = canonical(&json!([target,v["context"]]));
        let Some(state) = states.get(&key) else { continue };
        // FUR derive already excludes unresolved evidence. No parallel reducer.
        if state["metric"] != "W" { continue; }
        let w = number(&state["value"], "W")?;
        if w <= neutral { continue; }
        let accepted = state["experiences"].as_array().ok_or("pair evidence required")?
            .iter().any(|e| e["resolution_status"] == "accepted" && e["effective_weight"].as_f64().unwrap_or(0.0)>0.0);
        if !accepted { continue; }
        // Canonical identity, including persisted edge ID, has no semantic weight.
        let identity = canonical(&json!([primary,edge["target"],edge["edge_type"],v["context"],edge["edge_id"]]));
        let id = format!("{:x}", Sha256::digest(identity.as_bytes()));
        if best.as_ref().map_or(true, |(bw,bi,_)| w>*bw || (w==*bw && id<*bi)) {
            best=Some((w,id.clone(),json!({"source":primary,"target":edge["target"],"relation":edge["edge_type"],"context":v["context"],"W":w,"edge_id":edge["edge_id"],"canonical_edge_id":id,"role":"PAIRED"})));
        }
    }
    Ok(best.map(|(_,_,p)|p).unwrap_or(Value::Null))
}
