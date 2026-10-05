#[path = "../src/domain/mod.rs"]
mod domain;
use domain::*;
use serde_json::{json, Value};
fn policy() -> Value {
    json!({"kappa_u":4.0,"u0":0.5,"epsilon":0.0,"u_max":1.0,"kappa_w":4.0,"w0":0.5,"severity":{"contributed":1.0,"irrelevant":1.0,"misleading":2.0},"cluster":{"session_window":1800.0,"time_window":60.0}})
}
fn step(c: &Value, a: f64, q: f64, d: f64, u: f64, dt: f64) -> Value {
    dynamics::step(&json!({"config":c,"activation":a,"Q":q,"direct":d,"U":u,"elapsed":dt})).unwrap()
}
fn f(v: &Value, k: &str) -> f64 {
    v[k].as_f64().unwrap()
}
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> f64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        (self.0 >> 11) as f64 / ((1u64 << 53) as f64)
    }
}
#[test]
fn randomized_120000_bounded_monotone_neutral_zero() {
    let mut rng = Rng(0xa5074b);
    let c = dynamics::defaults();
    for _ in 0..120000 {
        let a = 0.02 + 0.96 * rng.next();
        let q = rng.next();
        let d = rng.next();
        let u = rng.next();
        let dt = 100.0 * rng.next();
        let r = step(&c, a, q, d, u, dt);
        assert!((0.02..=0.98).contains(&f(&r, "activation")));
        assert_eq!(f(&r, "activation") + f(&r, "latent"), 1.0);
        let lo = step(&c, a, q, d, 0., dt);
        let hi = step(&c, a, q, d, 1., dt);
        assert!(
            f(&lo, "activation") <= f(&r, "activation") + 2e-15
                && f(&r, "activation") <= f(&hi, "activation") + 2e-15
        );
        assert_eq!(f(&step(&c, a, q, d, 0.5, dt), "Q_prime"), q);
        let z = step(&c, a, 0., d, u, dt);
        assert_eq!(f(&z, "u"), 0.);
        assert_eq!(
            f(&z, "activation"),
            f(&step(&c, a, 0., d, 0., dt), "activation")
        );
        assert!(f(&z, "activation") <= a + 1e-15);
    }
}
#[test]
fn strong_cue_recovery_24000() {
    let mut rng = Rng(0x5567);
    for _ in 0..24000 {
        let mut c = dynamics::defaults();
        c["epsilon_a"] = json!(0.001 + 0.1 * rng.next());
        c["threshold"] = json!(0.2 + 0.25 * rng.next());
        c["delta"] = json!(0.001 + 0.1 * rng.next());
        c["rho"] = json!(0.01 + 5. * rng.next());
        c["q_min"] = json!(0.75 + 0.25 * rng.next());
        c["d_min"] = json!(0.75 + 0.25 * rng.next());
        c["alpha"] = json!(0.);
        let g = dynamics::validate_config(c.clone()).unwrap()["guarantees"].clone();
        c["alpha"] = json!(f(&g, "alpha_upper_exclusive") * rng.next() * 0.999);
        let g = dynamics::validate_config(c.clone()).unwrap()["guarantees"].clone();
        let dt = f(&g, "crossing_time_from_floor") * 1.00001 + 1e-8;
        let r = step(
            &c,
            f(&c, "epsilon_a"),
            f(&c, "q_min"),
            f(&c, "d_min"),
            0.,
            dt,
        );
        assert!(f(&r, "activation") > f(&c, "threshold"));
    }
}
#[test]
fn boundary_grid_60480() {
    let c = dynamics::defaults();
    let bound = f(
        &dynamics::validate_config(c.clone()).unwrap()["guarantees"],
        "alpha_upper_exclusive",
    );
    let mut count = 0;
    for alpha in [0., 1e-12, 0.25, bound * 0.99, bound - 1e-10, bound - 1e-8] {
        for u in [0., 1e-12, 0.25, 0.5, 0.75, 1. - 1e-12, 1.] {
            for q in [0., 1e-12, 0.01, 0.2, 0.5, 0.8, 0.99, 1.] {
                for d in [0., 0.1, 0.4, 0.8, 1.] {
                    for a in [0.02, 0.02000001, 0.4, 0.7, 0.97999999, 0.98] {
                        for dt in [0., 1e-12, 1e-6, 0.01, 1., 100.] {
                            let mut cfg = c.clone();
                            cfg["alpha"] = json!(alpha);
                            let r = step(&cfg, a, q, d, u, dt);
                            assert!((0.02..=0.98).contains(&f(&r, "activation")));
                            assert!(
                                f(&r, "Q_prime") >= (1. - alpha) * q - 1e-15
                                    && f(&r, "Q_prime") <= (1. + alpha) * q + 1e-15
                            );
                            count += 1;
                        }
                    }
                }
            }
        }
    }
    assert_eq!(count, 60480);
    for alpha in [-1., 1., bound, bound + 1e-12] {
        let mut cfg = c.clone();
        cfg["alpha"] = json!(alpha);
        assert!(dynamics::validate_config(cfg).is_err());
    }
}
fn initial() -> Value {
    json!({"reports":{},"experiences":{},"policy":policy()})
}
fn cmd(state: &Value, action: &str, payload: Value, id: &str, rev: u64) -> Value {
    json!({"state":state,"events":[],"action":action,"payload":payload,"actor":"admin","admin":true,"event_id":id,"now":100.,"request_id":id,"fingerprint":id,"revision":rev,"expected_revision":rev})
}
fn payload(kind: &str, ids: Value, typ: &str) -> Value {
    json!({"target":{"kind":kind,"memory_ids":ids,"relation":if kind=="pair"{json!("explains")}else{Value::Null}},"context":"research","feedback_type":typ})
}
fn commit(state: &Value, event: &Value) -> Value {
    let mut e = event.clone();
    if e["report"].is_null() {
        e.as_object_mut().unwrap().remove("report");
    }
    fur::reduce(json!({"state":state,"event":e})).unwrap()
}
#[test]
fn duplicate_retry_12000_and_correction_12000() {
    for i in 0..12000 {
        let state = initial();
        let id = format!("event-{i}");
        let c = cmd(
            &state,
            "report",
            payload("memory", json!([format!("m{i}")]), "CONTRIBUTED"),
            &id,
            0,
        );
        let ev = fur::apply(c.clone()).unwrap();
        let st = commit(&state, &ev);
        let mut retry = c.clone();
        let mut committed = ev.clone();
        committed["actor"] = json!("admin");
        committed["request_id"] = json!(id);
        committed["fingerprint"] = json!(id);
        retry["events"] = json!([committed]);
        assert!(fur::apply(retry).unwrap().get("retry").is_some());
        let dup = fur::apply(cmd(&st, "report", c["payload"].clone(), "dup", 1)).unwrap();
        let ds = commit(&st, &dup);
        let before = fur::derive(&st["experiences"], &policy()).unwrap();
        let after = fur::derive(&ds["experiences"], &policy()).unwrap();
        assert_eq!(
            before.as_object().unwrap().values().next().unwrap()["N_eff"],
            after.as_object().unwrap().values().next().unwrap()["N_eff"]
        );
        let exp = &ev["experiences"][0]["id"];
        let corr=fur::apply(cmd(&ds,"resolve",json!({"experience_id":exp,"feedback_type":"MISLEADING","status":"accepted","reason":"correction"}),"correction",2)).unwrap();
        let cs = commit(&ds, &corr);
        let projection = fur::derive(&cs["experiences"], &policy()).unwrap();
        let s = projection.as_object().unwrap().values().next().unwrap();
        assert_eq!(f(s, "N_eff"), 2.);
        assert!((f(s, "value") - 1. / 3.).abs() < 1e-15);
        assert_eq!(
            fur::derive(&cs["experiences"], &policy()).unwrap(),
            projection
        );
    }
}
#[test]
fn unresolved_unused_group_direction_merge_split() {
    let st = initial();
    let e = fur::apply(cmd(
        &st,
        "report",
        payload("memory", json!(["a"]), "CONTRIBUTED"),
        "a",
        0,
    ))
    .unwrap();
    let st = commit(&st, &e);
    let bad = fur::apply(cmd(
        &st,
        "report",
        payload("memory", json!(["a"]), "IRRELEVANT"),
        "b",
        1,
    ))
    .unwrap();
    let st = commit(&st, &bad);
    let p = fur::derive(&st["experiences"], &policy()).unwrap();
    assert_eq!(p.as_object().unwrap().values().next().unwrap()["N_eff"], 0.);
    let eid = &e["experiences"][0]["id"];
    let split=fur::apply(cmd(&st,"split",json!({"experience_id":eid,"partitions":[[e["report"]["id"]],[bad["report"]["id"]]],"reason":"distinct outcomes"}),"split",2)).unwrap();
    let st = commit(&st, &split);
    let ids: Vec<_> = split["experiences"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|x| x["active"] == true)
        .map(|x| x["id"].clone())
        .collect();
    assert_eq!(ids.len(), 2);
    let merge = fur::apply(cmd(
        &st,
        "merge",
        json!({"experience_ids":ids,"reason":"same outcome"}),
        "merge",
        3,
    ))
    .unwrap();
    let st = commit(&st, &merge);
    assert_eq!(
        fur::derive(&st["experiences"], &policy())
            .unwrap()
            .as_object()
            .unwrap()
            .values()
            .next()
            .unwrap()["N_eff"],
        0.
    );
    for (kind, ids, typ) in [
        ("memory", json!(["unused"]), "UNUSED"),
        ("group", json!(["a", "b"]), "GROUP_SUCCESS"),
    ] {
        let e = fur::apply(cmd(
            &initial(),
            "report",
            payload(kind, ids, typ),
            "unused",
            0,
        ))
        .unwrap();
        let st = commit(&initial(), &e);
        let states = fur::derive(&st["experiences"], &policy()).unwrap();
        assert!(states
            .as_object()
            .unwrap()
            .values()
            .all(|s| s["N_eff"] == 0.));
    }
    let mut st = initial();
    for (id, ids, typ) in [
        ("ab", json!(["a", "b"]), "PAIR_HELPED"),
        ("ba", json!(["b", "a"]), "PAIR_IRRELEVANT"),
    ] {
        let e = fur::apply(cmd(&st, "report", payload("pair", ids, typ), id, 0)).unwrap();
        st = commit(&st, &e);
    }
    let p = fur::derive(&st["experiences"], &policy()).unwrap();
    let values: Vec<_> = p
        .as_object()
        .unwrap()
        .values()
        .map(|s| f(s, "value"))
        .collect();
    assert_eq!(values, vec![0.6, 0.4]);
}
#[test]
fn repeated_cycles_relaxation_and_config_rejection() {
    let cfg = dynamics::defaults();
    for u in [0., 0.5, 1.] {
        let mut a = 0.02;
        for _ in 0..1000 {
            a = f(&step(&cfg, a, 0.8, 0.8, u, 0.1), "activation");
        }
        let eq = f(&step(&cfg, a, 0.8, 0.8, u, 0.), "equilibrium");
        assert!((a - eq).abs() < 1e-12);
        for _ in 0..1000 {
            a = f(&step(&cfg, a, 0., 0., u, 0.1), "activation");
        }
        assert!((a - 0.02).abs() < 1e-12);
    }
    for (k, v) in [
        ("rho", json!(0)),
        ("delta", json!(-1)),
        ("max_elapsed", json!(0)),
        ("epsilon_a", json!(0.5)),
        ("threshold", json!(0.02)),
        ("max_results", json!(true)),
        ("token_budget", json!(1)),
    ] {
        let mut c = cfg.clone();
        c[k] = v;
        assert!(dynamics::validate_config(c).is_err());
    }
}
#[test]
fn context_truth_invariants() {
    for v in [json!(null), json!(" research "), json!("LADC")] {
        assert_eq!(call("context", v.clone()).unwrap(), v);
    }
    for v in [json!("  "), json!(false), json!(1)] {
        assert!(call("context", v).is_err());
    }
    assert!(call(
        "context_write",
        json!({"data":{"primary_context":"A"},"supplied":"B"})
    )
    .is_err());
    for x in [0., 0.5, 1.] {
        let t = call(
            "truth",
            json!({"data":{"confidence":x,"U":x},"annotations":[],"open_conflict":false}),
        )
        .unwrap();
        assert_eq!(t["status"], "provisional");
        assert_eq!(t["source"], "epistemic_ledger");
        assert_eq!(t["score"], 0.0);
    }
}


fn ella_evidence(id:&str,reference:&str)->Value {
    json!({"id":id,"evidence_id":id,"content_hash":format!("hash-{id}"),
        "reference":reference,"source_type":"reported","origin":Value::Null,
        "session_id":Value::Null,"event_id":Value::Null,"derived_from":[]})
}
fn ella_assessment(id:&str,eid:&str,polarity:&str,strength:&str)->Value {
    json!({"assessment_id":id,"target_memory_id":"m","target_memory_version":"v",
        "evidence_id":eid,"assessor_id":format!("assessor-{id}"),
        "assessment_note":"bounded semantic judgment","polarity":polarity,
        "strength":strength,"status":"accepted"})
}
fn ella_project(evidence:Value,assessments:Value,conflict:&str)->Value {
    ella::project(&json!({"policy":ella::defaults(),"evidence":evidence,
        "assessments":assessments,"hard_groups":[],"target_memory_id":"m",
        "target_memory_version":"v","revision":1,"conflict_overlay":conflict})).unwrap()
}

#[test]
fn ella_dependency_min_strength_and_conflict_firewall() {
    let e1=ella_evidence("e1","same-artifact");
    let e2=ella_evidence("e2","same-artifact");
    let a1=ella_assessment("a1","e1","SUPPORTS","STRONG");
    let a2=ella_assessment("a2","e2","SUPPORTS","MEDIUM");
    let base=ella_project(json!([e1,e2]),json!([a1,a2]),"none");
    assert_eq!(base["raw_evidence_count"],2);
    assert_eq!(base["accepted_unit_count"],1);
    assert_eq!(base["hard_collapsed_count"],1);
    assert!((f(&base,"score")-3.0_f64.ln()).abs()<1e-15);
    let open=ella_project(
        json!([ella_evidence("e1","same-artifact"),ella_evidence("e2","same-artifact")]),
        json!([ella_assessment("a2","e2","SUPPORTS","MEDIUM"),ella_assessment("a1","e1","SUPPORTS","STRONG")]),
        "open");
    assert_eq!(f(&open,"score"),f(&base,"score"));
    assert_eq!(open["base_epistemic_verdict"],base["base_epistemic_verdict"]);
    assert_eq!(open["public_epistemic_state"],"DISPUTED");
    assert_eq!(open["units"],base["units"]);
}

#[test]
fn ella_mixed_polarity_unit_is_unresolved_and_no_evidence_is_distinct() {
    let mixed=ella_project(
        json!([ella_evidence("e1","same"),ella_evidence("e2","same")]),
        json!([ella_assessment("a1","e1","SUPPORTS","STRONG"),
               ella_assessment("a2","e2","OPPOSES","STRONG")]),"none");
    assert_eq!(mixed["accepted_unit_count"],0);
    assert_eq!(mixed["score"],0.0);
    assert_eq!(mixed["evidence_dispute"],true);
    assert_eq!(mixed["assessment_started"],true);
    let empty=ella_project(json!([]),json!([]),"none");
    assert_eq!(empty["score"],0.0);
    assert_eq!(empty["assessment_started"],false);
    assert_eq!(empty["evidence_dispute"],false);
}

#[test]
fn ella_rejects_arbitrary_numeric_strength_and_source_type_has_no_weight() {
    let mut bad=ella_assessment("a","e","SUPPORTS","WEAK");
    bad["strength"]=json!(1000.0);
    assert!(ella::assessment(bad).is_err());

    let weak=ella_project(json!([
        {"id":"e1","evidence_id":"e1","content_hash":"h1","reference":"one",
         "source_type":"experimentally_verified","origin":Value::Null,"session_id":Value::Null,
         "event_id":Value::Null,"derived_from":[]},
        {"id":"e2","evidence_id":"e2","content_hash":"h2","reference":"two",
         "source_type":"reported","origin":"person","session_id":Value::Null,
         "event_id":Value::Null,"derived_from":[]}
    ]),json!([
        ella_assessment("a1","e1","SUPPORTS","WEAK"),
        ella_assessment("a2","e2","SUPPORTS","WEAK")
    ]),"none");
    assert!((f(&weak,"score")-2.0*1.5_f64.ln()).abs()<1e-15);
}


#[test]
fn ella_unknown_origin_is_not_soft_dependency_or_lineage_coverage() {
    let mut p=ella::defaults();
    p["soft_same_origin"]=json!(true);
    let out=ella::project(&json!({"policy":p,"evidence":[
        {"id":"e1","evidence_id":"e1","content_hash":"h1","reference":"",
         "origin":"unknown","session_id":Value::Null,"event_id":Value::Null,"derived_from":[]},
        {"id":"e2","evidence_id":"e2","content_hash":"h2","reference":"",
         "origin":"unknown","session_id":Value::Null,"event_id":Value::Null,"derived_from":[]}
    ],"assessments":[
        ella_assessment("a1","e1","SUPPORTS","WEAK"),
        ella_assessment("a2","e2","SUPPORTS","WEAK")
    ],"hard_groups":[],"target_memory_id":"m","target_memory_version":"v",
      "revision":1,"conflict_overlay":"none"})).unwrap();
    assert_eq!(out["accepted_unit_count"],2);
    assert_eq!(out["soft_cluster_count"],0);
    assert_eq!(out["lineage_coverage"],0.0);
    assert_eq!(out["unresolved_independence_count"],2);
}


#[test]
fn ella_native_state_reducer_validates_persisted_lifecycle() {
    let state=json!({"assessments":{},"evidence_overrides":{},"hard_groups":{},
        "policy":ella::defaults(),"carried":{}});
    assert_eq!(ella::state(state.clone()).unwrap(),state);
    let assessment=json!({"assessment_id":"evt:assessment","target_memory_id":"m",
        "target_memory_version":"v","evidence_id":"e","assessor_id":"assessor",
        "assessment_note":"bounded semantic judgment","polarity":"SUPPORTS",
        "strength":"WEAK","request_id":"req","revision":1,"created_at":1.0,
        "status":"accepted"});
    let mut next=state.clone();
    next["assessments"]["evt:assessment"]=assessment;
    let payload=json!({"target_memory_id":"m","target_memory_version":"v",
        "evidence_id":"e","polarity":"SUPPORTS","strength":"WEAK",
        "assessment_note":"bounded semantic judgment"});
    let event=json!({"id":"evt","actor":"assessor","request_id":"req","action":"report",
        "payload":payload,"state":next});
    let reduced=ella::reduce(json!({"state":state,"event":event})).unwrap();
    assert_eq!(reduced["assessments"]["evt:assessment"]["status"],"accepted");

    // A structurally valid state that changes the semantic strength without a
    // matching command is rejected: replay is a native transition check, not
    // merely deserialization of a Python-produced snapshot.
    let mut tampered=event.clone();
    tampered["state"]["assessments"]["evt:assessment"]["strength"]=json!("STRONG");
    assert!(ella::reduce(json!({"state":json!({"assessments":{},"evidence_overrides":{},"hard_groups":{},
        "policy":ella::defaults(),"carried":{}}),"event":tampered})).is_err());
}
