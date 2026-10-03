"""Normal-server Settings + genuine SSE consolidated-chain acceptance; fictional data only."""
import hashlib,json,os,socket,subprocess,sys,tempfile,time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, expect

def main():
    repo=Path(__file__).resolve().parents[1]
    proof={};errors=[]
    with tempfile.TemporaryDirectory(prefix='ember-consolidated-') as tmp:
        root=Path(tmp)/'store';log=open(Path(tmp)/'server.log','w');sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close();url=f'http://127.0.0.1:{port}'
        server=subprocess.Popen([sys.executable,'-m','uvicorn','embers.api:app','--host','127.0.0.1','--port',str(port),'--timeout-graceful-shutdown','1'],env={**os.environ,'EMBER_STORE':str(root)},stdout=log,stderr=subprocess.STDOUT,cwd=repo)
        client=httpx.Client(base_url=url,trust_env=False,timeout=20)
        try:
            for _ in range(150):
                try:client.get('/v1/research/settings/demo');break
                except httpx.HTTPError:time.sleep(.1)
            auth=json.loads((Path(tmp)/'store-candidate-credentials.json').read_text());headers={'X-Ember-Agent-Id':auth['agent_id'],'X-Ember-Token':auth['token']}
            def post(path,body):
                r=client.post(path,headers=headers,json=body);r.raise_for_status();return r.json()
            def hashes():return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            rid=post('/v1/memory/write',{'namespace':'demo','content':{'content':'Fictional latent reactivation evidence','verify_status':'hypothesis'},'subject':'Ember research','primary_context':'mathematics'})['id']
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,executable_path=os.environ.get('EMBER_BROWSER_EXECUTABLE'))
                page=browser.new_page(viewport={'width':1440,'height':900});page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url+'/research/settings');page.locator('#namespace').fill('demo');page.locator('#agent').fill(auth['agent_id']);page.locator('#token').fill(auth['token']);page.locator('#auth button').click();expect(page.locator('#recovery')).to_contain_text('PASS')
                assert page.locator('.field').count()>=20
                before=hashes();page.locator('[data-path="config.alpha"]').fill('.99');page.locator('#validate').click();expect(page.locator('#recovery')).to_contain_text('FAIL');assert page.locator('#save').is_disabled();assert hashes()==before;proof['invalid_config_no_write']=True
                page.locator('[data-path="config.alpha"]').fill('.3');page.locator('#validate').click();expect(page.locator('#recovery')).to_contain_text('PASS');assert hashes()==before
                page.locator('#reason').fill('Fictional consolidation browser validation');page.locator('#save').click();expect(page.locator('#revision')).to_have_text('1');proof['settings_commit_revision']=1
                page.locator('#token').fill('');page.locator('#agent').fill('');page.locator('main').evaluate('(e)=>e.scrollTop=0');page.screenshot(path=str(repo/'docs/research-settings.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');proof['mobile_no_horizontal_overflow']=True
                grant=post('/v1/observer-access',{});view=browser.new_page(viewport={'width':1440,'height':900});view.on('pageerror',lambda e:errors.append(str(e)));requests=[];view.on('request',lambda r:requests.append(r.url.split('#')[0]));wire=[];cdp=view.context.new_cdp_session(view);cdp.send('Network.enable');cdp.on('Network.eventSourceMessageReceived',lambda e:wire.append(e['data']));view.goto(url+grant['viewer_path']);expect(view.locator('#status')).to_have_text('LIVE')
                base=sum('/v1/observer/events' in x for x in requests)
                feedback=post('/v1/usefulness/demo',{'action':'report','request_id':'helpful','payload':{'target':{'kind':'memory','memory_ids':[rid]},'context':'mathematics','feedback_type':'CONTRIBUTED'}})
                args={'query_id':'query1','direct_scores':{rid:.8},'elapsed':1,'context':'mathematics'}
                start=time.monotonic();result=post('/v1/research/recall/demo',args);expect(view.locator('#timeline')).to_contain_text('ember_research_recall');proof['activation_push_latency_seconds']=time.monotonic()-start
                assert post('/v1/research/recall/demo',args)==result
                view.locator('.territory g[role=button]').first.click();assert 'Q′' in view.locator('#inspector').inner_text()
                observed=view.evaluate("[...events.values()].find(e=>e.dynamics?.length).dynamics");assert observed==result['dynamics'];proof['browser_matches_committed_rust_dynamics']=True
                count=view.locator('.event').count();before=hashes();view.wait_for_timeout(6100);assert sum('/v1/observer/events' in x for x in requests)==base;assert hashes()==before;assert view.locator('.event').count()==count
                proof.update(healthy_snapshot_GETs=0,observer_mutated_memory=False,raw_SSE='\n'.join(wire),dynamics=result['dynamics'],retry_exact=True)
                for secret in (auth['token'],grant['code']):assert secret not in proof['raw_SSE']
                view.screenshot(path=str(repo/'docs/consolidated-observer.png'),full_page=True)
                browser.close()
            assert not errors,errors;proof['javascript_errors']=errors;proof['result']='PASS'
            (repo/'docs/consolidated-browser-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
            print(json.dumps({k:v for k,v in proof.items() if k not in ('raw_SSE','dynamics')},indent=2))
        finally:
            server.terminate();server.wait(timeout=10);client.close();log.close()
if __name__=='__main__':main()
