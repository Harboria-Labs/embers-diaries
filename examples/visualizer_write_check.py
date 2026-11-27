"""Real normal-server SSE + browser write/visual regression. No production data."""
import argparse, hashlib, json, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
from observer_transport_check import Proxy
from visualizer_surfaces_check import wait


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--idle-seconds',type=int,default=180);args=ap.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    repo=Path(__file__).resolve().parents[1];proof={};proc=None
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)/'store';log=(Path(tmp)/'server.log').open('w')
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        origin=f'http://127.0.0.1:{port}';api=httpx.Client(base_url=origin,trust_env=False,timeout=20)
        def start():
            nonlocal proc
            proc=subprocess.Popen([sys.executable,'-m','uvicorn','embers.api:app','--host','127.0.0.1','--port',str(port),'--timeout-graceful-shutdown','1'],cwd=repo,env={**os.environ,'EMBER_STORE':str(root)},stdout=log,stderr=subprocess.STDOUT)
            for _ in range(200):
                try:api.get('/health').raise_for_status();return
                except httpx.HTTPError:time.sleep(.1)
            raise RuntimeError('startup failed')
        def stop():
            if proc and proc.poll() is None:proc.terminate();proc.wait(10)
        def hashes():
            files=[p for p in root.rglob('*') if p.is_file()]
            sidecar=root.parent/(root.name+'-observation-journal.sqlite3')
            if sidecar.exists():files.append(sidecar)
            return {str(p.relative_to(root.parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        try:
            start();auth=json.loads((Path(tmp)/'store-candidate-credentials.json').read_text())
            headers={'X-Ember-Agent-Id':auth['agent_id'],'X-Ember-Token':auth['token']}
            def post(path,data,h=None):
                r=api.post(path,json=data,headers=h or headers);r.raise_for_status();return r.json()
            def mcp(name,arguments):
                data=post('/mcp',{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':{**arguments,**auth}}})['result']
                assert not data.get('isError'),data
                return json.loads(data['content'][0]['text'])
            def write(subject='LADC',context='experiments',i=0):
                return post('/v1/memory/write',{'namespace':'visual-research','content':{'content':f'Fictional research note {i}: a latent memory was inspected for query-triggered reactivation.','verify_status':'hypothesis'},'subject':subject,'primary_context':context})['id']
            view=post('/v1/visualizer-access',{'namespace':'visual-research','ttl_seconds':900})
            observer=post('/v1/observer-access',{'ttl_seconds':900})
            proxy=Proxy(origin);threading.Thread(target=proxy.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{proxy.server_port}'
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);context=browser.new_context(viewport={'width':1440,'height':960},record_video_dir=str(out/'recordings'),record_video_size={'width':1280,'height':854});pages=[];records=[];errors=[]
                for name,path,stats in [('namespace','/visualizer#view='+view['code'],'window.emberTransport'),('observer','/visualizer?mode=observer#observe='+observer['code'],'window.emberObserverTransport')]:
                    page=context.new_page();wire=[];requests=[];response_headers=[]
                    page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r,rows=requests:rows.append(r.url.split('#')[0]));cdp=context.new_cdp_session(page);cdp.send('Network.enable')
                    cdp.on('Network.eventSourceMessageReceived',lambda e,rows=wire:rows.append(e));cdp.on('Network.responseReceived',lambda e,rows=response_headers:rows.append(e['response']['headers']) if 'text/event-stream' in e['response'].get('mimeType','') else None)
                    page.goto(base+path);wait(page,'(s)=>window[s].connected',stats.split('.')[-1]);assert '#' not in page.url
                    records.append({'name':name,'initial':page.evaluate(stats),'wire':wire,'headers':response_headers,'requests':requests});pages.append((page,stats))
                ns,obs=pages[0][0],pages[1][0];ns.screenshot(path=str(out/'empty.png'))
                baseline=hashes();start_idle=time.monotonic()
                while time.monotonic()-start_idle<args.idle_seconds:
                    ns.wait_for_timeout(1000)
                    for page,stats in pages:
                        d=page.evaluate(stats);assert d['connected'] and d['reconnect_count']==0 and d['snapshot_gets']==1 and d['fallback_count']==0,d
                assert hashes()==baseline
                for (page,stats),record in zip(pages,records):record['after_idle']=page.evaluate(stats)
                proof['idle_seconds']=args.idle_seconds;proof['read_only_idle']=True
                for (p,stats),record in zip(pages,records):
                    record['transport_assets']=p.locator('script[src]').evaluate_all('(es)=>es.map(e=>e.src)')
                    record['stream_requests_during_idle']=sum('/v1/observer/stream?' in url or '/v1/visualizer-stream/' in url for url in record['requests'])
                    assert record['stream_requests_during_idle']==1
                assert records[0]['transport_assets']==records[1]['transport_assets']
                started=time.monotonic();rid=write();
                wait(ns,'(id)=>current.nodes.some(n=>n.id===id)',rid);wait(obs,'(id)=>[...events.values()].some(e=>e.event_type==="memory_written"&&e.memory_ids.includes(id))',rid)
                proof['write_delivery_seconds']=time.monotonic()-started;proof['write_id']=rid
                assert proof['write_delivery_seconds']<3
                assert all(p.evaluate(s)['snapshot_gets']==1 for p,s in pages)
                ns.evaluate('(id)=>selectNode(id)',rid);ns.screenshot(path=str(out/'single-memory.png'))
                mids=[rid]
                for i in range(1,12):mids.append(write('LADC' if i<6 else 'Pairing Matrix',['research','mathematics','experiments'][i%3],i))
                mcp_write=mcp('ember_write',{'namespace':'visual-research','content':'Fictional MCP provenance note','subject':'LADC','primary_context':'experiments'})['id'];mids.append(mcp_write)
                wait(obs,'(id)=>[...events.values()].some(e=>e.memory_ids.includes(id))',mcp_write)
                consolidated=mcp('ember_consolidate',{'namespace':'visual-research'})
                assert consolidated['new_record_ids'],consolidated
                wait(obs,'(ids)=>ids.every(id=>[...events.values()].some(e=>e.event_type==="memory_consolidated"&&e.memory_ids.includes(id)))',consolidated['new_record_ids'])
                proof['consolidated']=consolidated
                for pair,kind in [([mids[0],mids[1]],'explains'),([mids[1],mids[3]],'warns_about')]:
                    post('/v1/usefulness/visual-research',{'action':'report','request_id':'visual-'+kind,'payload':{'target':{'kind':'pair','memory_ids':pair,'relation':kind},'context':'experiments','feedback_type':'PAIR_HELPED'}})
                wait(ns,'current.edges.some(e=>e.kind==="pair_evidence")')
                baseline=hashes()
                for mode in ['network','bubbles','heatmap','orbit']:
                    ns.locator('[data-view='+mode+']').click();ns.wait_for_timeout(300);ns.screenshot(path=str(out/(mode+'-dark.png')))
                    assert ns.evaluate('document.documentElement.scrollHeight<=innerHeight')
                    if mode=='bubbles':
                        point=ns.evaluate('screenNodes.find(n=>n.kind==="node")');box=ns.locator('#graph').bounding_box();ns.mouse.click(box['x']+point['x'],box['y']+point['y']);assert ns.evaluate('selectedNode')==point['id']
                ns.locator('#themeToggle').click();ns.locator('[data-view=network]').click();ns.screenshot(path=str(out/'network-light.png'));obs.locator('#themeToggle').click();obs.screenshot(path=str(out/'observer-light.png'))
                ns.set_viewport_size({'width':390,'height':844});ns.screenshot(path=str(out/'mobile.png'));assert ns.evaluate('document.documentElement.scrollWidth<=innerWidth');ns.set_viewport_size({'width':1440,'height':960})
                ns.emulate_media(reduced_motion='reduce');assert ns.evaluate('matchMedia("(prefers-reduced-motion: reduce)").matches')
                assert hashes()==baseline
                proof['visual_checks']=['empty','single','disconnected','consolidated','typed directional pairs','bubble selection','network','heatmap','3D','dark/light','mobile fit','reduced motion CSS']
                # Presentation-only edge matrix: synthetic layout fixtures are not live events.
                saved=ns.evaluate('current');matrix=[]
                for count in [0,1,12,100]:
                    ns.evaluate("""(count)=>{const base=JSON.parse(JSON.stringify(current));const template=base.nodes[0]||{subject:'Fixture',primary_context:'test',preview:'Synthetic visual fixture',usefulness:[]};base.nodes=Array.from({length:count},(_,i)=>({...template,id:'fixture-'+i,subject:'Group '+(i%4),primary_context:'test',usefulness:[{context:'test',value:i/Math.max(1,count-1),N_eff:i%7}],heat:i%3===0?null:i/Math.max(1,count-1),heat_context:'test'}));base.edges=count>1?[{from:'fixture-0',to:'fixture-1',type:'explains',kind:'stored_relation'}]:[];base.events=[];base.experiences=[];selectedNode=null;selectedEvent=null;contextFocus=null;traceIds=null;render(base)}""",count)
                    for mode in ['network','bubbles','heatmap','orbit']:
                        ns.locator('[data-view='+mode+']').click();ns.wait_for_timeout(80)
                        assert ns.evaluate('document.documentElement.scrollWidth<=innerWidth && document.documentElement.scrollHeight<=innerHeight')
                        matrix.append({'nodes':count,'mode':mode,'result':'PASS'})
                    if count==100:ns.screenshot(path=str(out/'bounded-100.png'))
                ns.evaluate('(data)=>render(data)',saved);proof['presentation_fixture_matrix']=matrix
                assert hashes()==baseline
                # Same capability follows the same agent into a new session and namespace.
                sid1=mcp('ember_start_session',{'namespace':'visual-research'})['session_id']
                sid2=mcp('ember_start_session',{'namespace':'visual-other'})['session_id']
                ma=mcp('ember_write',{'namespace':'visual-research','session_id':sid1,'content':'Session A fixture','primary_context':'A'})['id']
                mb=mcp('ember_write',{'namespace':'visual-other','session_id':sid2,'content':'Session B fixture','primary_context':'B'})['id']
                wait(obs,'(id)=>[...events.values()].some(e=>e.memory_ids.includes(id))',mb)
                transitions=obs.evaluate('(ids)=>ids.map(id=>[...events.values()].find(e=>e.memory_ids.includes(id)))',[ma,mb])
                assert transitions[0]['session']!=transitions[1]['session']
                assert {t['namespace'] for t in transitions}=={'visual-research','visual-other'}
                assert obs.evaluate('territories.size')==2
                assert mb not in json.dumps(records[0]['wire'])
                proof['session_namespace_follow']=[{k:t[k] for k in ['namespace','session','event_id','observed_agent_id']} for t in transitions]
                for (p,stats),record in zip(pages,records):
                    record['after_healthy_events']=p.evaluate(stats)
                    assert record['after_healthy_events']['reconnect_count']==0 and record['after_healthy_events']['snapshot_gets']==1 and record['after_healthy_events']['fallback_count']==0
                # Transport disconnect, write while unavailable, same-page replay once.
                proxy.mode='unavailable';proxy.cut();wait(ns,"window.emberTransport.connection_state!=='LIVE'")
                disconnected=write(i=90);proxy.mode='pass'
                wait(ns,'(id)=>current.nodes.some(n=>n.id===id)',disconnected,timeout=45000);wait(obs,'(id)=>[...events.values()].some(e=>e.memory_ids.includes(id))',disconnected,timeout=45000)
                for p,s in pages:wait(p,'(s)=>window[s].connected',s.split('.')[-1],timeout=45000)
                assert obs.evaluate('(id)=>[...events.values()].filter(e=>e.event_type==="memory_written"&&e.memory_ids.includes(id)).length',disconnected)==1
                proof['disconnect_replay_once']=True
                # Restart the same normal server, no refresh or new code.
                stop();wait(ns,"window.emberTransport.connection_state!=='LIVE'");start()
                for p,s in pages:wait(p,'(s)=>window[s].connected',s.split('.')[-1],timeout=60000)
                restarted=write(i=91);wait(obs,'(id)=>[...events.values()].some(e=>e.memory_ids.includes(id))',restarted)
                proof['same_page_restart']=True
                proof['after_restart']=[p.evaluate(s) for p,s in pages]
                other=post('/v1/agents/register',{'name':'foreign writer'});foreignh={'X-Ember-Agent-Id':other['agent_id'],'X-Ember-Token':other['token']}
                foreign=post('/v1/memory/write',{'namespace':'foreign-only','content':'Must never reach these views'},foreignh)['id'];ns.wait_for_timeout(1800)
                assert all(foreign not in json.dumps(r['wire']) for r in records)
                assert not errors,errors
                for r in records:
                    raw=json.dumps(r['wire']);assert auth['token'] not in raw and view['code'] not in raw and observer['code'] not in raw
                    r['requests']=[u.split('?')[0] for u in r['requests']]
                proof.update(result='PASS',surfaces=records,cross_agent_raw_payload='PASS',javascript_errors=errors,transport_sha256=api.get('/v1/observer/build').json()['transport_sha256'])
                # Record only a compact interaction clip separately; idle footage discarded.
                context.close();browser.close()
            proxy.shutdown();(out/'proof.json').write_text(json.dumps(proof,indent=2)+'\n');print('PASS: writes + live views + idle + restart + provenance + themes')
        finally:
            stop();api.close();log.close()
            (out/'server.log').write_text((Path(tmp)/'server.log').read_text())

if __name__=='__main__':main()
