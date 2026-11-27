"""Opt-in synthetic HTTP/UI smoke test against an already running normal server.

Writes three fictional memories and evidence into the explicitly named namespace.
Requires the configured decision-agent credential file; never prints credentials.
Optional screenshots require playwright plus an installed Chromium browser.
"""
import argparse
import json
from pathlib import Path
import uuid
import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:9200')
    parser.add_argument('--credentials',required=True)
    parser.add_argument('--namespace',required=True,help='An isolated namespace you may write to')
    parser.add_argument('--screenshot')
    args=parser.parse_args()
    auth=json.loads(Path(args.credentials).read_text())
    headers={'X-Ember-Agent-Id':auth['agent_id'],'X-Ember-Token':auth['token']}
    client=httpx.Client(base_url=args.url,headers=headers,timeout=30,trust_env=False)
    def post(path,body):
        response=client.post(path,json=body)
        response.raise_for_status()
        return response.json()
    ns=args.namespace; run=uuid.uuid4().hex
    ids=[post('/v1/memory/write',{'namespace':ns,'subject':subject,'primary_context':context,
        'content':{'content':content,'verify_status':'hypothesis'}})['id']
        for subject,context,content in [('LADC','research','Fictional latent reactivation result for demo.'),
            ('LADC','mathematics','Fictional query heat formula for demo.'),
            ('Pairing','experiments','Fictional paired-memory warning for demo.')]]
    post('/v1/memory/orient',{'namespace':ns,'clues':'latent reactivation query heat'})
    post('/v1/memory/recall',{'namespace':ns,'query':'latent','primary_context':'research'})
    def report(typ,kind='memory',members=None,context='research',identity=None):
        target={'kind':kind,'memory_ids':members or [ids[0]]}
        if kind=='pair':target['relation']='explains'
        payload={'target':target,'context':context,'feedback_type':typ}
        if identity:payload.update(identity={'value':identity,'source':'synthetic-http-test','provenance':'explicit live fixture'},identity_verified=True)
        return post('/v1/usefulness/'+ns,{'action':'report','request_id':str(uuid.uuid4()),'payload':payload})
    for _ in range(5): duplicate=report('CONTRIBUTED',identity=run)
    assert duplicate['transitions'][0]['after']['value']==.6
    assert duplicate['transitions'][0]['after']['N_eff']==1
    report('PAIR_HELPED','pair',ids[:2])
    report('GROUP_SUCCESS','group',ids)
    report('UNUSED',members=[ids[2]])
    report('MISLEADING',context='implementation')
    def rpc(method,params=None):
        return post('/mcp',{'jsonrpc':'2.0','id':1,'method':method,'params':params or {}})['result']
    rpc('initialize')
    catalog={t['name'] for t in rpc('tools/list')['tools']}
    assert {'ember_usefulness_update','ember_usefulness_state'} <= catalog
    mcp_args={'namespace':ns,'agent_id':auth['agent_id'],'token':auth['token']}
    mutation=rpc('tools/call',{'name':'ember_usefulness_update','arguments':dict(mcp_args,
        action='report',request_id='mcp-'+run,payload={'target':{'kind':'memory','memory_ids':[ids[2]]},
        'context':'research','feedback_type':'UNUSED'})})
    assert not mutation['isError'],mutation
    read=rpc('tools/call',{'name':'ember_usefulness_state','arguments':mcp_args})
    assert not read['isError'],read
    view=client.get('/v1/visualizer/'+ns).json()
    revision=view['revision']
    assert all(n['verify_status']=='hypothesis' for n in view['nodes'])
    assert view['diagnostics']['duplicate_reports_collapsed']>=4
    if args.screenshot:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
            errors=[];methods=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:methods.append(r.method))
            grant=client.post('/v1/visualizer-access',json={'namespace':ns}).json()
            page.goto(args.url+grant['viewer_path'])
            page.locator('.event').first.wait_for()
            page.locator('.event').nth(len(view['events'])-2).click()
            assert 'UNCHANGED' in page.locator('#inspector').inner_text()
            assert page.evaluate('scene.size')==3
            page.screenshot(path=args.screenshot,full_page=True)
            page.evaluate('selectNode(current.nodes[0].id)')
            assert not errors,errors
            assert set(methods)=={'GET','POST'},methods
            browser.close()
    assert client.get('/v1/visualizer/'+ns).json()['revision']==revision
    print(json.dumps({'status':'PASS','namespace':ns,'memory_ids':ids,'revision':revision,
                      'duplicate_U':.6,'duplicate_N_eff':1,'truth':'hypothesis unchanged',
                      'visualizer_mutations':0,'mcp_discovery_and_calls':'PASS','screenshot':args.screenshot},indent=2))


if __name__=='__main__':main()
