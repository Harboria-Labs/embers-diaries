use super::*;

pub fn defaults() -> Value {
    json!({"model_version":MODEL,"alpha":0.25,"epsilon_a":0.02,"rho":1.0,"delta":0.05,"threshold":0.4,"q_min":0.8,"d_min":0.8,"model_time_unit":"explicit model unit","max_elapsed":1000000.0,"max_seeds":32,"max_state_memories":2048,"max_results":10,"token_budget":2048,"memory_token_cap":512,"neighborhood_token_cap":1024})
}
pub fn validate_config(v: Value) -> Result<Value> {
    let d = defaults();
    keys(
        &v,
        &d.as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect::<Vec<_>>(),
        true,
    )?;
    if v["model_version"] != MODEL || v["model_time_unit"] != "explicit model unit" {
        return Err("unsupported model version/time unit".into());
    }
    let get = |k: &str| number(&v[k], k);
    let (a, e, rho, delta, t, q, direct) = (
        get("alpha")?,
        get("epsilon_a")?,
        get("rho")?,
        get("delta")?,
        get("threshold")?,
        get("q_min")?,
        get("d_min")?,
    );
    if !(0.0..1.0).contains(&a)
        || !(0.0 < e && e < 0.5)
        || rho <= 0.0
        || delta <= 0.0
        || !(e < t && t < 1.0 - e)
        || !(0.0 < q && q <= 1.0)
        || !(0.0..=1.0).contains(&direct)
        || get("max_elapsed")? <= 0.0
    {
        return Err("invalid activation parameter domain".into());
    }
    let ratio = (t - e) / (1.0 - 2.0 * e);
    let limit = 1.0 - (ratio / (1.0 - ratio)) * ((1.0 - direct) + delta) / q;
    if !(a < limit) {
        return Err(format!(
            "strong-cue recovery fails: alpha must be < {limit}"
        ));
    }
    for (key, cap) in [
        ("max_seeds", 512),
        ("max_state_memories", 100000),
        ("max_results", 100),
        ("token_budget", 1000000),
        ("memory_token_cap", 1000000),
        ("neighborhood_token_cap", 1000000),
    ] {
        let n = v[key]
            .as_u64()
            .ok_or_else(|| format!("{key} must be positive integer"))?;
        if n == 0 || n > cap {
            return Err(format!("{key} exceeds bounds"));
        }
    }
    if v["max_state_memories"].as_u64() < v["max_seeds"].as_u64()
        || v["memory_token_cap"].as_u64() > v["token_budget"].as_u64()
        || v["neighborhood_token_cap"].as_u64() > v["token_budget"].as_u64()
    {
        return Err("incompatible admission budgets".into());
    }
    let u = rho * q * (1.0 - a);
    let opp = rho * ((1.0 - direct) + delta);
    let total = u + opp;
    if !total.is_finite() || total <= 0.0 {
        return Err("rates exceed numerical range".into());
    }
    let eq = e + (1.0 - 2.0 * e) * u / total;
    let crossing = ((eq - e) / (eq - t)).ln() / total;
    if !eq.is_finite() || !crossing.is_finite() {
        return Err("recovery guarantee exceeds numerical range".into());
    }
    Ok(
        json!({"config":v,"guarantees":{"strong_cue_recovery":"PASS","alpha_upper_exclusive":limit,"worst_case_equilibrium":eq,"threshold":t,"crossing_time_from_floor":crossing,"crossing_time_units":"explicit model unit","assumption":"sustained Q >= q_min and d >= d_min; fixed configuration"}}),
    )
}
pub fn step(v: &Value) -> Result<Value> {
    let cfg = &v["config"];
    validate_config(cfg.clone())?;
    let get = |k: &str| number(&v[k], k);
    let (a, q, d, u, dt) = (
        get("activation")?,
        get("Q")?,
        get("direct")?,
        get("U")?,
        get("elapsed")?,
    );
    let e = cfg["epsilon_a"].as_f64().unwrap();
    let alpha = cfg["alpha"].as_f64().unwrap();
    let rho = cfg["rho"].as_f64().unwrap();
    let delta = cfg["delta"].as_f64().unwrap();
    let threshold = cfg["threshold"].as_f64().unwrap();
    if !(e..=1.0 - e).contains(&a)
        || !(0.0..=1.0).contains(&u)
        || !(0.0..=1.0).contains(&q)
        || !(0.0..=1.0).contains(&d)
        || dt < 0.0
        || dt > cfg["max_elapsed"].as_f64().unwrap()
    {
        return Err("invalid activation input".into());
    }
    let b = 2.0 * u - 1.0;
    let multiplier = 1.0 + alpha * b;
    let qp = q * multiplier;
    let excitation = rho * qp;
    let opposition = rho * ((1.0 - d) + delta);
    let total = excitation + opposition;
    if !total.is_finite() || total <= 0.0 {
        return Err("rates exceed numerical range".into());
    }
    let equilibrium = e + (1.0 - 2.0 * e) * excitation / total;
    let next = (a + (equilibrium - a) * (-(-total * dt).exp_m1())).clamp(e, 1.0 - e);
    Ok(
        json!({"Q":q,"direct":d,"U":u,"B":b,"alpha":alpha,"multiplier":multiplier,"Q_prime":qp,"u":excitation,"v":opposition,"activation_before":a,"equilibrium":equilibrium,"activation":next,"latent":1.0-next,"threshold":threshold,"stage":if next>=threshold{"ACTIVE"}else{"LATENT"},"reactivated":a<threshold&&next>=threshold,"elapsed":dt,"model_version":MODEL}),
    )
}
// Admission is a deterministic protocol: Python supplies exact tokenizer counts for
// each proposed rendered sequence; Rust owns ordering, all limits and the decision.
pub fn admit(v: &Value) -> Result<Value> {
    let cfg = &v["config"];
    validate_config(cfg.clone())?;
    if let Some(tokens) = v.get("final_tokens") {
        let total = tokens
            .as_u64()
            .ok_or("final token count must be nonnegative integer")?;
        if total > cfg["token_budget"].as_u64().unwrap() {
            return Err("context wrapper exceeds token budget".into());
        }
        return Ok(json!({"within_budget":true}));
    }
    let rows = v["rows"].as_array().ok_or("rows required")?;
    if rows.len() > cfg["max_seeds"].as_u64().unwrap() as usize {
        return Err("candidate bound exceeded".into());
    }
    let mut ranked: Vec<_> = rows
        .iter()
        .filter(|r| r["Q"].as_f64().unwrap_or(0.0) > 0.0)
        .collect();
    ranked.sort_by(|a, b| {
        let score = |r: &Value| {
            r["Q_prime"].as_f64().unwrap_or(0.0) * r["activation"].as_f64().unwrap_or(0.0)
        };
        score(b)
            .total_cmp(&score(a))
            .then_with(|| a["id"].as_str().cmp(&b["id"].as_str()))
    });
    let mut latent: Vec<_> = ranked
        .iter()
        .copied()
        .filter(|r| {
            r["activation_before"].as_f64().unwrap_or(0.0) < cfg["threshold"].as_f64().unwrap()
        })
        .collect();
    latent.sort_by_key(|r| r["id"].as_str());
    let seq = v["sequence"].as_u64().ok_or("sequence required")?;
    if seq == 0 {
        return Err("sequence must be positive".into());
    }
    let reserved = if latent.is_empty() {
        None
    } else {
        Some(latent[((seq - 1) as usize) % latent.len()]["id"].clone())
    };
    let mut order = Vec::new();
    if let Some(id) = &reserved {
        order.push(id.clone());
    }
    for row in ranked {
        if Some(&row["id"]) != reserved.as_ref() && row["stage"] == "ACTIVE" {
            order.push(row["id"].clone())
        }
    }
    if v.get("item_tokens").is_none() {
        return Ok(json!({"order":order,"latent_inspected":reserved}));
    }
    let item = v["item_tokens"]
        .as_u64()
        .ok_or("item token count must be nonnegative integer")?;
    let total = v["total_tokens"]
        .as_u64()
        .ok_or("total token count must be nonnegative integer")?;
    let count = v["selected_count"]
        .as_u64()
        .ok_or("selected_count required")?;
    // No neighborhood expansion in v1: each direct candidate is its own neighborhood.
    Ok(
        json!({"admit":item<=cfg["memory_token_cap"].as_u64().unwrap()&&item<=cfg["neighborhood_token_cap"].as_u64().unwrap()&&total<=cfg["token_budget"].as_u64().unwrap()&&count<cfg["max_results"].as_u64().unwrap(),"order":order,"latent_inspected":reserved}),
    )
}

pub fn batch(v: &Value) -> Result<Value> {
    let cfg = &v["config"];
    validate_config(cfg.clone())?;
    context(&v["context"])?;
    let scores = v["scores"]
        .as_object()
        .ok_or("direct_scores must be object")?;
    if scores.len() > cfg["max_seeds"].as_u64().unwrap() as usize {
        return Err("seed budget exceeded".into());
    }
    let prior = v["prior"]
        .as_object()
        .ok_or("prior activation object required")?;
    let usefulness = v["usefulness"]
        .as_object()
        .ok_or("usefulness projection required")?;
    let mut ids: BTreeSet<String> = prior.keys().cloned().collect();
    ids.extend(scores.keys().cloned());
    let mut state = serde_json::Map::new();
    let mut rows = vec![];
    let seq = v["sequence"].as_u64().ok_or("sequence required")?;
    for id in ids {
        let q = if scores.contains_key(&id) {
            number(&scores[&id], "direct score")?
        } else {
            0.0
        };
        let key =
            canonical(&json!([{"kind":"memory","memory_ids":[id],"relation":null},v["context"]]));
        let st = usefulness.get(&key);
        let p = &v["fur_policy"];
        let u = st.map(|s| s["value"].as_f64().unwrap()).unwrap_or(
            p["epsilon"].as_f64().unwrap()
                + (p["u_max"].as_f64().unwrap() - p["epsilon"].as_f64().unwrap())
                    * p["u0"].as_f64().unwrap(),
        );
        let a = prior
            .get(&id)
            .map(|p| p["activation"].clone())
            .unwrap_or(cfg["epsilon_a"].clone());
        let mut r = step(
            &json!({"config":cfg,"activation":a,"Q":q,"direct":q,"U":u,"elapsed":v["elapsed"]}),
        )?;
        r["id"] = json!(id);
        r["context"] = v["context"].clone();
        r["N_eff"] = st.map(|s| s["N_eff"].clone()).unwrap_or(json!(0.0));
        state.insert(id.clone(),json!({"activation":r["activation"],"last_query":q,"last_seen":if scores.contains_key(&id){json!(seq)}else{prior[&id]["last_seen"].clone()}}));
        rows.push(r);
    }
    let mut keep: Vec<_> = state.keys().cloned().collect();
    keep.sort_by(|a, b| {
        state[b]["last_seen"]
            .as_u64()
            .cmp(&state[a]["last_seen"].as_u64())
            .then(a.cmp(b))
    });
    keep.truncate(cfg["max_state_memories"].as_u64().unwrap() as usize);
    let retained: BTreeSet<_> = keep.into_iter().collect();
    state.retain(|id, _| retained.contains(id));
    Ok(json!({"state":state,"rows":rows,"model_version":MODEL,"pair_expansion":"NOT CONNECTED"}))
}
use std::collections::BTreeSet;
