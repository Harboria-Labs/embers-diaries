"""Consolidated model persistence/IO. All numerical decisions are native Rust."""
from copy import deepcopy
import json
import os
from ..core.domain import call, MODEL_VERSION, explicit_truth
from ..cognitive.usefulness import derive, digest, KIND
from .usefulness_service import service


def settings(db, namespace, actor):
    db.require_namespace_access(namespace, actor, 'read')
    ledger=service(db, namespace)
    with db._writer.lock:
        state=ledger._load()
        result=call('config',state.get('research_config') or call('config_default',None))
        from .research_schema import schema
        from ..cognitive.usefulness import UsefulnessPolicy
        from dataclasses import asdict
        # Read-only projection of the existing journal; no new configuration store.
        recorded = [event for event in ledger._events if event.get('action') in ('research_config', 'configure')]
        history = [{key: deepcopy(event[key]) for key in
                    ('id', 'revision', 'configuration_revision', 'actor', 'created_at', 'reason', 'research_config', 'policy')
                    if key in event} for event in reversed(recorded[-30:])]
        return dict(result, configuration_history=history, history_limit=30,
            history_truncated=len(recorded)>30, fields=schema(call('config_default',None),asdict(UsefulnessPolicy())), policy=deepcopy(state['policy']), model_version=MODEL_VERSION,
            configuration_revision=state.get('configuration_revision',0), journal_revision=len(ledger._events),
            defaults=call('config_default',None), can_edit=actor in ledger.admins)


def configure(db, namespace, actor, body):
    if set(body)!={'config','policy','request_id','expected_revision','reason'}:
        raise ValueError('config, policy, request_id, expected_revision and reason required')
    return service(db,namespace).apply('research_config',{'config':body['config'],'policy':body['policy'],'reason':body['reason']},
        actor=actor,request_id=body['request_id'],expected_revision=body['expected_revision'])


def _render(rows, format):
    if format=='structured':return json.dumps(rows,ensure_ascii=False,separators=(',',':'),sort_keys=True,allow_nan=False)
    text='\n\n'.join(f"[{r['id']}; truth={r['truth_status']}; source={r['written_by']}]\n{json.dumps(r['data'],ensure_ascii=False,sort_keys=True)}" for r in rows)
    if format=='messages':return json.dumps([{'role':'system','content':text}],ensure_ascii=False,separators=(',',':'))
    if format!='text':raise ValueError('unsupported context format')
    return text


def recall(db, namespace, actor, *, query_id, direct_scores, elapsed, context=None, format='structured', session_id=None):
    db.require_namespace_access(namespace, actor, 'write')
    call('context',context)
    if not isinstance(query_id,str) or not query_id or len(query_id)>128:raise ValueError('query_id must be 1..128 characters')
    if session_id:
        s=db.get_session(session_id)
        if s is None or s.agent_id!=actor or s.status.value!='active':raise PermissionError('active own session required')
    request=dict(direct_scores=direct_scores,elapsed=elapsed,context=context,format=format,session_id=session_id)
    fingerprint=digest([actor,'activation',request])
    ledger=service(db,namespace)
    with db._writer.lock:
        state=ledger._load()
        # Native guard owns exact retry identity; replay returns the original response.
        guard=call('research_guard',dict(events=[e if e['actor']==actor and e['request_id']==query_id else {k:e[k] for k in ('actor','request_id','fingerprint')} for e in ledger._events],actor=actor,request_id=query_id,fingerprint=fingerprint))
        if guard.get('retry') is not None:return guard['retry']['response']
        cfg=state.get('research_config') or call('config_default',None)
        old=deepcopy(state.get('activation_state',{}));key=json.dumps(context,ensure_ascii=False)
        scoped=old.get(key,{'sequence':0,'memories':{}})
        learned=derive(state['experiences'],state['policy'])
        batch=call('activation_batch',dict(config=cfg,context=context,prior=scoped['memories'],scores=direct_scores,
            usefulness=learned,fur_policy=state['policy'],sequence=scoped['sequence']+1,elapsed=elapsed))
        records={}
        for rid in direct_scores:
            rec=db._reader.get(rid,track_access=False)
            if rec is None or rec.namespace!=namespace or rec.record_type not in db._DURABLE_MEMORY_TYPES or not rec.retrieval_candidate:
                raise ValueError('direct target is unavailable in this namespace')
            records[rid]=rec
        candidates=[r for r in batch['rows'] if r['id'] in records]
        admission=dict(config=cfg,rows=candidates,sequence=scoped['sequence']+1)
        plan=call('admit',admission)
        import tiktoken
        encoding=tiktoken.get_encoding(os.environ.get('EMBER_TOKEN_ENCODING','cl100k_base'))
        counter=lambda text:len(encoding.encode(text,disallowed_special=()))
        selected=[]
        for rid in plan['order']:
            rec=records[rid]
            truth=explicit_truth(rec,any(c.status.value in ('open','investigating') for c in db.conflicts_for(rid)))
            row={'id':rid,'data':rec.data,'truth_status':truth['status'],'truth_projection':truth,'written_by':rec.written_by,'content_hash':rec.content_hash,
                 'dynamics':next(r for r in candidates if r['id']==rid)}
            if call('admit',dict(admission,item_tokens=counter(_render([row],format)),total_tokens=counter(_render(selected+[row],format)),selected_count=len(selected)))['admit']:
                selected.append(row)
        direct_ids=[r['id'] for r in selected]
        from .pairing import select
        paired, pair_route=select(db,namespace,direct_ids,context,state,query_id)
        pair_expansion=None
        if paired is not None:
            truth=explicit_truth(paired,any(c.status.value in ('open','investigating') for c in db.conflicts_for(paired.id)))
            row={'id':paired.id,'data':paired.data,'truth_status':truth['status'],'truth_projection':truth,
                 'written_by':paired.written_by,'content_hash':paired.content_hash,'retrieval':pair_route}
            if call('admit',dict(admission,item_tokens=counter(_render([row],format)),total_tokens=counter(_render(selected+[row],format)),selected_count=len(selected)))['admit']:
                selected.append(row)
                pair_expansion=pair_route
        rendered=_render(selected,format)
        call('admit',dict(config=cfg,final_tokens=counter(rendered)))
        response=dict(context=rendered,format=format,token_count=counter(rendered),selected_ids=[r['id'] for r in selected],candidate_ids=list(records),
            query_id=query_id,primary_context=context,model_version=MODEL_VERSION,configuration_revision=state.get('configuration_revision',0),
            dynamics=batch['rows'],latent_inspected=plan['latent_inspected'],pair_expansion=pair_expansion,direct_ids=direct_ids,primary_memory_id=direct_ids[0] if direct_ids else None,tokenizer='tiktoken:'+encoding.name)
        old[key]={'sequence':scoped['sequence']+1,'memories':batch['state']}
        event={'kind':KIND,'id':ledger.prefix+f'{len(ledger._events)+1:012d}','revision':len(ledger._events)+1,
            'namespace':namespace,'actor':actor,'request_id':query_id,'action':'activation','fingerprint':fingerprint,'created_at':float(ledger.clock()),
            'previous':ledger._events[-1]['seal'] if ledger._events else None,'policy':state['policy'],'research_config':cfg,
            'configuration_revision':state.get('configuration_revision',0),'activation_state':old,'model_version':MODEL_VERSION,'response':response,
            'observation':{'operation':'ember_research_recall','context':context,'session_id':session_id,'query':None,
                'direct_ids':direct_ids,'pair_expansion':pair_expansion,'candidate_ids':list(records),'returned_ids':response['selected_ids'],'dynamics':batch['rows'],
                'observed_heat':{r['id']:r['activation'] for r in batch['rows']},'heat_source':MODEL_VERSION,
                'budget':{'token_budget':cfg['token_budget'],'token_count':response['token_count'],'tokenizer':response['tokenizer']},'latency_ms':None}}
        ledger._append(event)
        return response
