//! ELLA V1 reference reduction. No inference, clocks, IO, posterior or SPRT claims.
use super::*;
use std::collections::{BTreeMap,BTreeSet};
pub fn defaults()->Value {json!({"strengths":{"WEAK":1.5_f64.ln(),"MEDIUM":3.0_f64.ln(),"STRONG":5.0_f64.ln()},"T_verify":3.0,"T_disfavor":-3.0,"dispute_mass":1.0,"soft_same_origin":false,"soft_same_session":false,"max_evidence":10000,"max_assessments":20000})}
pub fn policy(v:Value)->Result<Value>{
 let w=number(&v["strengths"]["WEAK"],"WEAK")?;let m=number(&v["strengths"]["MEDIUM"],"MEDIUM")?;let s=number(&v["strengths"]["STRONG"],"STRONG")?;
 if !(0.0<w&&w<=m&&m<=s&&number(&v["T_verify"],"T_verify")?>0.0&&number(&v["T_disfavor"],"T_disfavor")?<0.0&&number(&v["dispute_mass"],"dispute_mass")?>0.0){return Err("invalid ELLA policy".into())}
 for k in ["soft_same_origin","soft_same_session"]{if !v[k].is_boolean(){return Err(format!("{k} must be boolean"))}}
 for k in ["max_evidence","max_assessments"]{if !v[k].as_u64().is_some_and(|x|x>0&&x<=100000){return Err(format!("invalid {k}"))}}
 Ok(v)
}
pub fn assessment(v:Value)->Result<Value>{
 for k in ["assessment_id","target_memory_id","target_memory_version","evidence_id","assessor_id","assessment_note"]{text(&v[k],k)?;}
 if !["SUPPORTS","OPPOSES"].contains(&v["polarity"].as_str().unwrap_or("")){return Err("invalid polarity".into())}
 if !["WEAK","MEDIUM","STRONG"].contains(&v["strength"].as_str().unwrap_or("")){return Err("invalid strength class".into())}
 Ok(v)
}
fn root(p:&BTreeMap<String,String>,x:&str)->String{let mut r=x.to_string();while p[&r]!=r {r=p[&r].clone();}r}
fn join(p:&mut BTreeMap<String,String>,a:&str,b:&str){if !p.contains_key(a)||!p.contains_key(b){return}let x=root(p,a);let y=root(p,b);if x!=y {let (lo,hi)=if x<y{(x,y)}else{(y,x)};p.insert(hi,lo);}}

pub fn state(v:Value)->Result<Value>{
 let obj=v.as_object().ok_or("ELLA state object required")?;
 let expected=["assessments","evidence_overrides","hard_groups","policy","carried"].into_iter().collect::<BTreeSet<_>>();
 let actual=obj.keys().map(String::as_str).collect::<BTreeSet<_>>();
 if actual!=expected{return Err("invalid ELLA state fields".into())}
 policy(v["policy"].clone())?;
 let assessments=v["assessments"].as_object().ok_or("assessments object required")?;
 for (id,a) in assessments{
   assessment(a.clone())?;
   if a["assessment_id"]!=*id{return Err("assessment key/id mismatch".into())}
   if !["accepted","confirmation_required","withdrawn","superseded","resolved"].contains(&a["status"].as_str().unwrap_or("")){return Err("invalid assessment status".into())}
   if let Some(parent)=a.get("confirmation_of").filter(|x|!x.is_null()){text(parent,"confirmation_of")?;}
   if let Some(flag)=a.get("requires_confirmation"){if !flag.is_boolean(){return Err("requires_confirmation must be boolean".into())}}
 }
 let overrides=v["evidence_overrides"].as_object().ok_or("evidence_overrides object required")?;
 for value in overrides.values(){
   let o=value.as_object().ok_or("evidence override object required")?;
   for key in o.keys(){if !["invalidated","reference","event_id","origin","derived_from","origin_confidence"].contains(&key.as_str()){return Err("unsupported evidence override field".into())}}
   if let Some(flag)=o.get("invalidated"){if !flag.is_boolean(){return Err("invalidated must be boolean".into())}}
   if let Some(conf)=o.get("origin_confidence"){if !["UNKNOWN","AGENT_DECLARED","SYSTEM_CONFIRMED"].contains(&conf.as_str().unwrap_or("")){return Err("invalid origin confidence".into())}}
   if let Some(deps)=o.get("derived_from"){let a=deps.as_array().ok_or("derived_from must be array")?;for d in a{text(d,"dependency")?;}}
 }
 let groups=v["hard_groups"].as_object().ok_or("hard_groups object required")?;
 for ids in groups.values(){let a=ids.as_array().ok_or("hard group must be array")?;if a.len()<2{return Err("hard group requires at least two evidence ids".into())}let mut seen=BTreeSet::new();for id in a{let s=text(id,"group evidence id")?;if !seen.insert(s){return Err("duplicate evidence id in hard group".into())}}}
 let carried=v["carried"].as_object().ok_or("carried object required")?;
 for ids in carried.values(){let a=ids.as_array().ok_or("carried evidence must be array")?;let mut seen=BTreeSet::new();for id in a{let s=text(id,"carried evidence id")?;if !seen.insert(s){return Err("duplicate carried evidence id".into())}}}
 Ok(v)
}
pub fn reduce(v:Value)->Result<Value>{
 state(v["state"].clone())?;
 let event=v["event"].as_object().ok_or("ELLA event object required")?;
 let next=event.get("state").cloned().ok_or("ELLA event state required")?;
 state(next)
}

pub fn project(v:&Value)->Result<Value>{
 let pol=policy(v["policy"].clone())?;let ev=v["evidence"].as_array().ok_or("evidence required")?;let aa=v["assessments"].as_array().ok_or("assessments required")?;
 if ev.len()>pol["max_evidence"].as_u64().unwrap() as usize||aa.len()>pol["max_assessments"].as_u64().unwrap() as usize{return Err("ELLA cap exceeded".into())}
 let mut evidence=BTreeMap::new();for e in ev{let id=text(&e["id"],"evidence record id")?.to_string();if evidence.insert(id,e).is_some(){return Err("duplicate evidence record ID".into())}}
 let mut parents:BTreeMap<String,String>=evidence.keys().map(|x|(x.clone(),x.clone())).collect();let mut identities=BTreeMap::<String,String>::new();let mut aliases=BTreeMap::<String,String>::new();let mut dangling=0;
 for (id,e) in &evidence {for field in ["evidence_id","content_hash","reference","event_id"]{if let Some(value)=e[field].as_str().filter(|s|!s.is_empty()){let key=format!("{field}:{value}");if let Some(prior)=identities.get(&key){join(&mut parents,id,prior)}else{identities.insert(key,id.clone());}}}if let Some(a)=e["evidence_id"].as_str(){aliases.insert(a.into(),id.clone());}}
 for (id,e) in &evidence {if let Some(deps)=e["derived_from"].as_array(){for d in deps{let name=text(d,"dependency")?;let target=if evidence.contains_key(name){Some(name.to_string())}else{aliases.get(name).cloned()};if let Some(target)=target{join(&mut parents,id,&target)}else{dangling+=1}}}}
 if let Some(groups)=v["hard_groups"].as_array(){for group in groups{let ids=group.as_array().ok_or("group must be array")?;if let Some(a)=ids.first(){for b in ids.iter().skip(1){join(&mut parents,text(a,"group id")?,text(b,"group id")?)}}}}
 let hard_count=parents.keys().map(|x|root(&parents,x)).collect::<BTreeSet<_>>().len();let mut soft=BTreeMap::<String,String>::new();let mut soft_links=0;
 for (id,e) in &evidence {for (enabled,field) in [("soft_same_origin","origin"),("soft_same_session","session_id")]{if pol[enabled]==true {if let Some(value)=e[field].as_str().filter(|s|!s.is_empty() && !(field=="origin" && *s=="unknown")){let k=format!("{field}:{value}");if let Some(prior)=soft.get(&k){if root(&parents,id)!=root(&parents,prior){soft_links+=1;join(&mut parents,id,prior)}}else{soft.insert(k,id.clone());}}}}}
 let mut units=BTreeMap::<String,Vec<&Value>>::new();let mut started=false;
 for a in aa {assessment(a.clone())?;if a["target_memory_id"]!=v["target_memory_id"]||a["target_memory_version"]!=v["target_memory_version"]{continue}started=true;if a["status"]!="accepted"{continue}if a["requires_confirmation"]==true && !aa.iter().any(|b| b["confirmation_of"]==a["assessment_id"] && b["status"]=="accepted" && b["assessor_id"]!=a["assessor_id"] && b["polarity"]==a["polarity"]) {continue}
 if let Some(parent)=a["confirmation_of"].as_str(){if !aa.iter().any(|b|b["assessment_id"]==parent && b["status"]=="accepted"){continue}}
 let eid=a["evidence_id"].as_str().unwrap();if let Some(e)=evidence.get(eid){if e["invalidated"]!=true {units.entry(root(&parents,eid)).or_default().push(a);}}}
 let mut plus=0.0;let mut minus=0.0;let mut accepted=0;let mut unresolved=0;let mut contributions=vec![];
 for (id,assessments) in units{let polarities=assessments.iter().map(|a|a["polarity"].as_str().unwrap()).collect::<BTreeSet<_>>();if polarities.len()!=1{unresolved+=1;contributions.push(json!({"unit_id":id,"status":"unresolved"}));continue}
 let magnitude=assessments.iter().map(|a|pol["strengths"][a["strength"].as_str().unwrap()].as_f64().unwrap()).fold(f64::INFINITY,f64::min);let polarity=polarities.first().unwrap();if *polarity=="SUPPORTS"{plus+=magnitude}else{minus+=magnitude}accepted+=1;let mut assessment_ids=assessments.iter().map(|a|a["assessment_id"].as_str().unwrap().to_string()).collect::<Vec<_>>();assessment_ids.sort();contributions.push(json!({"unit_id":id,"polarity":polarity,"magnitude":magnitude,"status":"accepted","assessment_ids":assessment_ids}));}
 let score=plus-minus;let base=if score>=pol["T_verify"].as_f64().unwrap(){"VERIFIED"}else if score<=pol["T_disfavor"].as_f64().unwrap(){"DISFAVORED"}else{"PROVISIONAL"};
 let dispute=(plus>=pol["dispute_mass"].as_f64().unwrap()&&minus>=pol["dispute_mass"].as_f64().unwrap())||unresolved>0;
 let conflict=v["conflict_overlay"].as_str().unwrap_or("none");let public=if dispute||["open","investigating"].contains(&conflict){"DISPUTED"}else{base};
 let covered=ev.iter().filter(|e|e["origin"].as_str().is_some_and(|s|!s.is_empty()&&s!="unknown")||e["reference"].as_str().is_some_and(|s|!s.is_empty())||e["event_id"].as_str().is_some_and(|s|!s.is_empty())).count();
 Ok(json!({"base_epistemic_verdict":base,"public_epistemic_state":public,"score":score,"support_mass":plus,"opposition_mass":minus,"raw_evidence_count":ev.len(),"accepted_unit_count":accepted,"hard_collapsed_count":ev.len()-hard_count,"soft_cluster_count":soft_links,"unresolved_independence_count":ev.len()-covered,"dangling_dependency_count":dangling,"lineage_coverage":if ev.is_empty(){0.0}else{covered as f64/ev.len() as f64},"assessment_started":started,"epistemic_revision":v["revision"],"evidence_dispute":dispute,"conflict_overlay":conflict,"units":contributions}))
}
