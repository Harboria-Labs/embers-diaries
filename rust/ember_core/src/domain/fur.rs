use super::*;
use std::collections::BTreeSet;
pub fn policy(v: Value) -> Result<Value> {
    keys(
        &v,
        &[
            "kappa_u", "u0", "epsilon", "u_max", "kappa_w", "w0", "severity", "cluster",
        ],
        true,
    )?;
    keys(
        &v["severity"],
        &["contributed", "irrelevant", "misleading"],
        true,
    )?;
    keys(&v["cluster"], &["session_window", "time_window"], true)?;
    for k in ["kappa_u", "kappa_w"] {
        if number(&v[k], k)? <= 0.0 {
            return Err("prior strength must be positive".into());
        }
    }
    for k in ["u0", "w0", "epsilon", "u_max"] {
        if !(0.0..=1.0).contains(&number(&v[k], k)?) {
            return Err("invalid prior or floor/ceiling".into());
        }
    }
    if number(&v["epsilon"], "epsilon")? >= number(&v["u_max"], "u_max")? {
        return Err("invalid floor/ceiling".into());
    }
    for k in ["contributed", "irrelevant", "misleading"] {
        if number(&v["severity"][k], k)? <= 0.0 {
            return Err("severities must be positive".into());
        }
    }
    if number(&v["severity"]["misleading"], "misleading")?
        <= number(&v["severity"]["irrelevant"], "irrelevant")?
    {
        return Err("misleading severity must exceed irrelevant severity".into());
    }
    for k in ["session_window", "time_window"] {
        if !(0.0..=86400.0).contains(&number(&v["cluster"][k], k)?) {
            return Err("cluster windows must be in [0,86400]".into());
        }
    }
    Ok(v)
}
pub fn target(mut v: Value) -> Result<Value> {
    keys(&v, &["kind", "memory_ids", "relation"], false)?;
    let kind = v["kind"].as_str().ok_or("target kind required")?.to_owned();
    let ids = v["memory_ids"]
        .as_array()
        .ok_or("target memory_ids required")?;
    if ids.is_empty()
        || ids.len() > 32
        || ids.iter().map(canonical).collect::<BTreeSet<_>>().len() != ids.len()
    {
        return Err("invalid target cardinality".into());
    }
    for id in ids {
        text(id, "memory ID")?;
    }
    if !["memory", "pair", "group"].contains(&kind.as_str())
        || (kind == "memory" && ids.len() != 1)
        || (kind == "pair" && ids.len() != 2)
    {
        return Err("invalid target cardinality".into());
    }
    if kind == "pair" {
        if !["explains", "requires", "warns_about", "alternative"]
            .contains(&v["relation"].as_str().unwrap_or(""))
        {
            return Err("pair requires supported directional type".into());
        }
    } else if !v["relation"].is_null() {
        return Err("only pair targets carry a relation".into());
    }
    if kind == "group" {
        v["memory_ids"]
            .as_array_mut()
            .unwrap()
            .sort_by(|a, b| a.as_str().cmp(&b.as_str()));
    }
    if v.get("relation").is_none() {
        v["relation"] = Value::Null;
    }
    Ok(v)
}
fn valid_type(kind: &str, t: &Value) -> bool {
    let ts = match kind {
        "memory" => vec!["CONTRIBUTED", "IRRELEVANT", "MISLEADING", "UNUSED"],
        "pair" => vec!["PAIR_HELPED", "PAIR_IRRELEVANT", "UNUSED"],
        "group" => vec!["GROUP_SUCCESS", "GROUP_FAILURE", "UNUSED"],
        _ => vec![],
    };
    ts.contains(&t.as_str().unwrap_or(""))
}
fn prior(t: &Value, c: &Value, p: &Value) -> Value {
    let pair = t["kind"] == "pair";
    json!({"target":t,"context":c,"positive_mass":0.0,"N_eff":0.0,"experiences":[],"metric":if pair{"W"}else{"U"},"value":if pair{p["w0"].as_f64().unwrap()}else{p["epsilon"].as_f64().unwrap()+(p["u_max"].as_f64().unwrap()-p["epsilon"].as_f64().unwrap())*p["u0"].as_f64().unwrap()}})
}
pub fn derive(experiences: &Value, p: &Value) -> Result<Value> {
    policy(p.clone())?;
    let exps = experiences
        .as_object()
        .ok_or("experiences object required")?;
    let mut out = serde_json::Map::new();
    for exp in exps.values() {
        if exp["active"] != true || exp["target"]["kind"] == "group" {
            continue;
        }
        let key = canonical(&json!([exp["target"], exp["context"]]));
        let st = out
            .entry(key)
            .or_insert_with(|| prior(&exp["target"], &exp["context"], p));
        let typ = &exp["resolved_feedback_type"];
        let mut w = 0.0;
        if exp["resolution_status"] == "accepted" && typ != "UNUSED" {
            if !valid_type(exp["target"]["kind"].as_str().unwrap_or(""), typ) {
                return Err("invalid accepted feedback type".into());
            }
            let positive = typ == "CONTRIBUTED" || typ == "PAIR_HELPED";
            w = p["severity"][if positive {
                "contributed"
            } else if typ == "MISLEADING" {
                "misleading"
            } else {
                "irrelevant"
            }]
            .as_f64()
            .unwrap();
            st["N_eff"] = json!(st["N_eff"].as_f64().unwrap() + w);
            if positive {
                st["positive_mass"] = json!(st["positive_mass"].as_f64().unwrap() + w);
            }
        }
        st["experiences"].as_array_mut().unwrap().push(json!({"id":exp["id"],"effective_weight":w,"resolution_status":exp["resolution_status"],"feedback_type":typ}));
    }
    for st in out.values_mut() {
        let pair = st["metric"] == "W";
        let k = p[if pair { "kappa_w" } else { "kappa_u" }]
            .as_f64()
            .unwrap();
        let pr = p[if pair { "w0" } else { "u0" }].as_f64().unwrap();
        let n = number(&st["N_eff"], "N_eff")?;
        let pos = number(&st["positive_mass"], "positive mass")?;
        if !(k + n).is_finite() {
            return Err("evidence mass exceeds finite numeric range".into());
        }
        let f = (k * pr + pos) / (k + n);
        st["value"] = json!(if pair {
            f
        } else {
            p["epsilon"].as_f64().unwrap()
                + (p["u_max"].as_f64().unwrap() - p["epsilon"].as_f64().unwrap()) * f
        });
    }
    Ok(Value::Object(out))
}
fn consensus(exp: &mut Value, reports: &Value) -> Result<()> {
    let mut types = BTreeSet::new();
    for r in exp["reports"].as_array().ok_or("report list required")? {
        let id = text(r, "report ID")?;
        let typ = reports[id]["feedback_type"]
            .as_str()
            .ok_or("missing report")?;
        if typ != "UNUSED" {
            types.insert(typ.to_string());
        }
    }
    if types.len() > 1 {
        exp["resolution_status"] = json!("unresolved");
        exp["resolved_feedback_type"] = Value::Null;
    } else {
        exp["resolution_status"] = json!("accepted");
        exp["resolved_feedback_type"] =
            json!(types.iter().next().map(String::as_str).unwrap_or("UNUSED"));
    }
    Ok(())
}
pub fn reduce(v: Value) -> Result<Value> {
    let mut state = v["state"].clone();
    let event = &v["event"];
    for k in [
        "research_config",
        "configuration_revision",
        "activation_state",
    ] {
        if let Some(val) = event.get(k) {
            state[k] = val.clone();
        }
    }
    if let Some(p) = event.get("policy") {
        policy(p.clone())?;
        state["policy"] = p.clone();
    }
    if let Some(r) = event.get("report") {
        state["reports"][text(&r["id"], "report ID")?] = r.clone();
    }
    if let Some(exps) = event["experiences"].as_array() {
        for e in exps {
            state["experiences"][text(&e["id"], "experience ID")?] = e.clone();
        }
    }
    Ok(state)
}
pub fn apply(v: Value) -> Result<Value> {
    let state = &v["state"];
    let p = &v["payload"];
    let action = text(&v["action"], "action")?;
    let actor = text(&v["actor"], "actor")?;
    let eid = text(&v["event_id"], "event ID")?;
    let now = number(&v["now"], "now")?;
    let admin = v["admin"] == true;
    text(&v["request_id"], "request_id")?;
    if !p.is_object() || canonical(p).len() > 16384 {
        return Err("payload must be an object of at most 16 KiB".into());
    }
    if ![
        "report",
        "resolve",
        "merge",
        "split",
        "configure",
        "research_config",
    ]
    .contains(&action)
    {
        return Err("unsupported usefulness action".into());
    }
    if action != "report" && !admin {
        return Err("AUTH:feedback decision requires a configured authorized agent".into());
    }
    if let Some(events) = v["events"].as_array() {
        for e in events {
            if e["actor"] == actor && e["request_id"] == v["request_id"] {
                if e["fingerprint"] != v["fingerprint"] {
                    return Err("request_id reused with different input".into());
                }
                return Ok(json!({"retry":e}));
            }
        }
    }
    if action != "report"
        && (v["expected_revision"].as_u64().is_none() || v["expected_revision"] != v["revision"])
    {
        return Err("stale expected_revision".into());
    }
    policy(state["policy"].clone())?;
    let mut working = state.clone();
    let mut changed = Vec::new();
    let mut report = Value::Null;
    if action == "report" {
        keys(
            p,
            &[
                "target",
                "context",
                "feedback_type",
                "session_id",
                "query_request_id",
                "identity",
                "identity_verified",
                "note",
                "experience_id",
            ],
            false,
        )?;
        let t = target(p["target"].clone())?;
        let context = &p["context"];
        if !context.is_null() {
            text(context, "context")?;
        }
        let typ = &p["feedback_type"];
        if !valid_type(t["kind"].as_str().unwrap(), typ) {
            return Err("feedback type does not match target".into());
        }
        let session = &p["session_id"];
        if !session.is_null() {
            text(session, "session_id")?;
        }
        let verified = p
            .get("identity_verified")
            .unwrap_or(&Value::Bool(false))
            .as_bool()
            .ok_or("identity_verified must be boolean")?;
        let identity = &p["identity"];
        if !identity.is_null() {
            keys(identity, &["value", "source", "provenance"], true)?;
            for k in ["value", "source", "provenance"] {
                text(&identity[k], k)?;
            }
        }
        if verified && (!admin || identity.is_null()) {
            return Err(
                "AUTH:only configured decision agents may explicitly attest identity".into(),
            );
        }
        report = json!({"id":format!("{eid}:report"),"target":t,"context":context,"feedback_type":typ,"actor":actor,"session_id":session,"query_request_id":p["query_request_id"],"identity":identity,"identity_verified_by":if verified{json!(actor)}else{Value::Null},"note":p.get("note").cloned().unwrap_or(json!("")),"created_at":now});
        let candidates: Vec<_> = state["experiences"]
            .as_object()
            .ok_or("experiences required")?
            .values()
            .filter(|e| e["active"] == true && e["target"] == t && e["context"] == *context)
            .collect();
        let found;
        if let Some(id) = p["experience_id"].as_str().filter(|s| !s.is_empty()) {
            found = candidates.iter().copied().find(|e| e["id"] == id);
            if found.is_none() {
                return Err("experience does not match target/context".into());
            }
        } else if verified {
            let key = json!([identity["source"], identity["value"]]);
            let matches: Vec<_> = candidates
                .iter()
                .copied()
                .filter(|e| {
                    e.get("identity_aliases")
                        .and_then(Value::as_array)
                        .map(|a| a.contains(&key))
                        .unwrap_or(e["identity_key"] == key)
                })
                .collect();
            if matches.len() > 1 {
                return Err("identity was split; explicit experience_id required".into());
            }
            found = matches.first().copied();
        } else {
            let window = state["policy"]["cluster"][if session.is_null() {
                "time_window"
            } else {
                "session_window"
            }]
            .as_f64()
            .unwrap();
            found = candidates.iter().rev().copied().find(|e| {
                let elapsed = now - e["created_at"].as_f64().unwrap_or(now);
                e["identity_key"].is_null()
                    && e["cluster_actor"] == actor
                    && e["cluster_session"] == *session
                    && elapsed >= 0.0
                    && elapsed <= window
                    && window > 0.0
            });
        }
        let mut exp=found.cloned().unwrap_or_else(||json!({"id":format!("{eid}:experience"),"target":t,"context":context,"reports":[],"active":true,"created_at":now,"updated_at":now,"identity_key":if verified{json!([identity["source"],identity["value"]])}else{Value::Null},"identity_provenance":if verified{identity.clone()}else{Value::Null},"identity_verified_by":if verified{json!(actor)}else{Value::Null},"cluster_actor":actor,"cluster_session":session,"identity_mode":if verified{"attested"}else{"structural"},"manual_resolution":false}));
        exp["reports"]
            .as_array_mut()
            .ok_or("reports required")?
            .push(report["id"].clone());
        exp["updated_at"] = json!(now);
        working["reports"][report["id"].as_str().unwrap()] = report.clone();
        if typ == "UNUSED" && exp["manual_resolution"] == true {
        } else if exp["manual_resolution"] == true {
            exp["resolution_status"] = json!("unresolved");
            exp["resolved_feedback_type"] = Value::Null;
            exp["manual_resolution"] = json!(false);
        } else {
            consensus(&mut exp, &working["reports"])?;
        }
        changed.push(exp);
    } else if action == "configure" || action == "research_config" {
        if action == "research_config" {
            keys(p, &["policy", "reason", "config"], true)?;
            super::dynamics::validate_config(p["config"].clone())?;
            if state["activation_state"]
                .as_object()
                .is_some_and(|m| !m.is_empty())
                && p["config"]["epsilon_a"] != state["research_config"]["epsilon_a"]
            {
                return Err("activation floor change requires explicit state migration; existing activation cannot teleport".into());
            }
        } else {
            keys(p, &["policy", "reason"], true)?;
        }
        text(&p["reason"], "reason")?;
        working["policy"] = policy(p["policy"].clone())?;
    } else {
        text(&p["reason"], "reason")?;
        if action == "resolve" {
            keys(
                p,
                &["experience_id", "feedback_type", "status", "reason"],
                true,
            )?;
            let id = text(&p["experience_id"], "experience_id")?;
            let mut exp = state["experiences"][id].clone();
            if exp["active"] != true {
                return Err("active experience required".into());
            }
            let status = p["status"].as_str().unwrap_or("");
            if !["accepted", "unresolved"].contains(&status)
                || (status == "accepted"
                    && !valid_type(
                        exp["target"]["kind"].as_str().unwrap_or(""),
                        &p["feedback_type"],
                    ))
                || (status == "unresolved" && !p["feedback_type"].is_null())
            {
                return Err("invalid resolved type".into());
            }
            exp["resolution_status"] = p["status"].clone();
            exp["resolved_feedback_type"] = p["feedback_type"].clone();
            exp["manual_resolution"] = json!(true);
            exp["updated_at"] = json!(now);
            exp["resolution_reason"] = p["reason"].clone();
            changed.push(exp);
        } else {
            keys(
                p,
                if action == "merge" {
                    &["experience_ids", "reason"]
                } else {
                    &["experience_id", "partitions", "reason"]
                },
                true,
            )?;
            let ids = if action == "merge" {
                p["experience_ids"].clone()
            } else {
                json!([p["experience_id"]])
            };
            let ids = ids.as_array().ok_or("invalid experience list")?;
            if ids.is_empty()
                || ids.len() > 100
                || ids.iter().map(canonical).collect::<BTreeSet<_>>().len() != ids.len()
                || (action == "merge" && ids.len() < 2)
            {
                return Err("invalid experience list".into());
            }
            let old: Vec<_> = ids
                .iter()
                .map(|id| text(id, "experience ID").map(|s| state["experiences"][s].clone()))
                .collect::<Result<_>>()?;
            if old.iter().any(|e| e["active"] != true) {
                return Err("active experiences required".into());
            }
            let template = &old[0];
            if old
                .iter()
                .any(|e| e["target"] != template["target"] || e["context"] != template["context"])
            {
                return Err("merge cannot cross target/context".into());
            }
            let all: Vec<Value> = old
                .iter()
                .flat_map(|e| e["reports"].as_array().unwrap().clone())
                .collect();
            let parts = if action == "merge" {
                json!([all])
            } else {
                p["partitions"].clone()
            };
            let parts = parts
                .as_array()
                .ok_or("nonempty report partitions required")?;
            if parts.is_empty()
                || (action == "split" && parts.len() < 2)
                || parts.iter().any(|p| p.as_array().is_none_or(Vec::is_empty))
            {
                return Err("nonempty report partitions required".into());
            }
            let flat: Vec<_> = parts
                .iter()
                .flat_map(|p| p.as_array().unwrap().iter().map(canonical))
                .collect();
            let expected: BTreeSet<_> = all.iter().map(canonical).collect();
            let actual: BTreeSet<_> = flat.iter().cloned().collect();
            if flat.len() != all.len() || actual.len() != flat.len() || actual != expected {
                return Err("partitions must conserve every report exactly once".into());
            }
            let mut aliases = BTreeSet::new();
            for e in &old {
                let keys = e
                    .get("identity_aliases")
                    .cloned()
                    .unwrap_or(json!([e["identity_key"]]));
                for k in keys.as_array().unwrap() {
                    if !k.is_null() {
                        aliases.insert(canonical(k));
                    }
                }
                let mut e = e.clone();
                e["active"] = json!(false);
                e["updated_at"] = json!(now);
                e["replaced_by"] = json!(eid);
                changed.push(e);
            }
            for (n, part) in parts.iter().enumerate() {
                let mut exp = template.clone();
                for (k,val) in json!({"id":format!("{eid}:experience:{n}"),"reports":part,"active":true,"created_at":now,"updated_at":now,"manual_resolution":false,"identity_key":null,"identity_aliases":aliases.iter().map(|s|serde_json::from_str::<Value>(s).unwrap()).collect::<Vec<_>>(),"identity_provenance":{"decision":action,"reason":p["reason"]},"identity_verified_by":actor,"identity_mode":"manual_partition","cluster_actor":null,"cluster_session":null,"parents":ids}).as_object().unwrap(){exp[k]=val.clone();}
                exp.as_object_mut().unwrap().remove("replaced_by");
                consensus(&mut exp, &working["reports"])?;
                changed.push(exp);
            }
        }
    }
    for exp in &changed {
        working["experiences"][exp["id"].as_str().unwrap()] = exp.clone();
    }
    let before = derive(&state["experiences"], &state["policy"])?;
    let after = derive(&working["experiences"], &working["policy"])?;
    let keys: BTreeSet<_> = before
        .as_object()
        .unwrap()
        .keys()
        .chain(after.as_object().unwrap().keys())
        .cloned()
        .collect();
    let report_key = canonical(&json!([report["target"], report["context"]]));
    let mut transitions = Vec::new();
    for key in keys {
        if before[&key] != after[&key] || (!report.is_null() && key == report_key) {
            let old = if before.get(&key).is_none() {
                prior(
                    &after[&key]["target"],
                    &after[&key]["context"],
                    &state["policy"],
                )
            } else {
                before[&key].clone()
            };
            transitions.push(json!({"key":serde_json::from_str::<Value>(&key).unwrap(),"before":old,"after":after[&key]}));
        }
    }
    let old_cfg = state
        .get("research_config")
        .filter(|v| !v.is_null())
        .cloned()
        .unwrap_or_else(super::dynamics::defaults);
    let new_cfg = if action == "research_config" {
        p["config"].clone()
    } else {
        old_cfg.clone()
    };
    for tr in &mut transitions {
        if tr["before"]["metric"] != "U" {
            continue;
        }
        let context_key = canonical(&tr["before"]["context"]);
        let rid = tr["before"]["target"]["memory_ids"][0]
            .as_str()
            .unwrap_or("");
        let q = state["activation_state"][&context_key]["memories"][rid]["last_query"].as_f64();
        let mut impacts =
            json!({"query_basis":"last recorded Q; prospective effect only; activation unchanged"});
        for (side, cfg) in [("before", &old_cfg), ("after", &new_cfg)] {
            if let Some(u) = tr[side]["value"].as_f64() {
                let b = 2.0 * u - 1.0;
                let mult = 1.0 + cfg["alpha"].as_f64().unwrap() * b;
                impacts[side] = json!({"B":b,"alpha":cfg["alpha"],"multiplier":mult,"Q":q,"Q_prime":q.map(|x|x*mult)});
            }
        }
        tr["modulation"] = impacts;
    }
    let report_state = if report.is_null() {
        Value::Null
    } else {
        let exp = changed.last().unwrap();
        json!({"report_id":report["id"],"experience_id":exp["id"],"resolution_status":exp["resolution_status"],"duplicate_report":exp["reports"].as_array().unwrap().len()>1,"report_count":exp["reports"].as_array().unwrap().len(),"effective_mass_changed":transitions.iter().any(|t|t["before"]["N_eff"]!=t["after"]["N_eff"])})
    };
    Ok(
        json!({"report_state":report_state,"policy":working["policy"],"experiences":changed,"report":report,"transitions":transitions,"model_version":MODEL,"research_config":if action=="research_config"{p["config"].clone()}else{old_cfg.clone()},"configuration_revision":state["configuration_revision"].as_u64().unwrap_or(0)+if action=="research_config"||action=="configure"{1}else{0}}),
    )
}
