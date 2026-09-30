"""Real normal-server/browser acceptance: one observer follows A -> B -> new session.
All mutations use fictional records in a temporary store, through separate agent APIs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import httpx
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screenshot');parser.add_argument('--proof')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ember-observer-') as tmp:
        root=Path(tmp)/'store';log=open(Path(tmp)/'server.log','w')
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        url=f'http://127.0.0.1:{port}';client=httpx.Client(base_url=url,trust_env=False,timeout=10);process=None
        def start():
            nonlocal process
            process=subprocess.Popen([sys.executable,'-m','uvicorn','embers.api:app','--host','127.0.0.1','--port',str(port),'--timeout-graceful-shutdown','1'],env={**os.environ,'EMBER_STORE':str(root)},stdout=log,stderr=subprocess.STDOUT)
            for _ in range(100):
                try:client.get('/visualizer').raise_for_status();return
                except httpx.HTTPError:time.sleep(.1)
            raise RuntimeError('Server startup failed: '+(Path(tmp)/'server.log').read_text()[-4000:])
        def stop():
            if process and process.poll() is None:process.terminate();process.wait(timeout=10)
        def hashes():return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
        try:
            start();auth=json.loads((Path(tmp)/'store-candidate-credentials.json').read_text())
            headers={'X-Ember-Agent-Id':auth['agent_id'],'X-Ember-Token':auth['token']}
            def post(path,body,actor_headers=None):
                r=client.post(path,json=body,headers=actor_headers or headers);r.raise_for_status();return r.json()
            other=post('/v1/agents/register',{'name':'unobserved-agent'})
            other_headers={'X-Ember-Agent-Id':other['agent_id'],'X-Ember-Token':other['token']}
            before=hashes();grant=post('/v1/observer-access',{})
            assert hashes()==before
            mutation_ids=[]
            def action(ns,session=None,actor_headers=None):
                rid=post('/v1/memory/write',{'namespace':ns,'content':{'content':'Fictional memory for '+ns,'verify_status':'hypothesis'},'subject':'Observation experiment','primary_context':'research'},actor_headers)['id']
                body={'action':'report','request_id':str(time.time_ns()),'payload':{'target':{'kind':'memory','memory_ids':[rid]},'context':'research','feedback_type':'CONTRIBUTED'}}
                if session:body['payload']['session_id']=session
                e=post('/v1/usefulness/'+ns,body,actor_headers);mutation_ids.append(e['id']);return e,rid
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':900})
                errors=[];requests=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append({'method':r.method,'url':r.url.split('#')[0]}))
                observer_wire=[]
                cdp=page.context.new_cdp_session(page);cdp.send('Network.enable')
                cdp.on('Network.eventSourceMessageReceived',lambda e:observer_wire.append(e['data']))
                def wait(expr,timeout=15000):
                    end=time.monotonic()+timeout/1000
                    while time.monotonic()<end:
                        if page.evaluate(expr):return
                        page.wait_for_timeout(50)
                    raise AssertionError(page.locator('#notice').inner_text())
                page.goto(url+grant['viewer_path']);wait("() => document.querySelector('#status').textContent==='LIVE'")
                build=client.get('/v1/observer/build').json()
                assert build['transport_client_version']=='9f94c5a / transport-v2'
                assert build['transport_asset_url'] in [r['url'].removeprefix(url) for r in requests]
                assert hashlib.sha256(client.get(build['transport_asset_url']).content).hexdigest()==build['transport_sha256']
                assert 'Observer client: 9f94c5a / transport-v2' in page.locator('#buildMarker').inner_text()
                assert page.evaluate('window.emberObserverTransport.transport_sha256')==build['transport_sha256']
                assert page.evaluate('window.emberObserverTransport.server_build')==build['server_build']
                assert page.locator('input[name=namespace]').input_value()==''
                get_count=lambda:sum('/v1/observer/events' in r['url'] for r in requests)
                baseline=get_count();assert baseline==1
                s1=post('/v1/sessions',{'namespace':'research-A','task':'fictional first session'})['session_id']
                start_time=time.monotonic();e1,r1=action('research-A',s1)
                wait("() => document.querySelector('#namespace').textContent==='research-A'",1500)
                latency=time.monotonic()-start_time
                page.evaluate('window.firstTerritory=document.querySelector("[data-namespace=research-A]")')
                s2=post('/v1/sessions',{'namespace':'research-B','task':'fictional new session'})['session_id']
                e2,r2=action('research-B',s2)
                wait("() => document.querySelector('#namespace').textContent==='research-B'",1500)
                assert page.evaluate('window.firstTerritory===document.querySelector("[data-namespace=research-A]")')
                assert page.locator('.territory').count()==2
                page.evaluate('apply([...events.values()][0])')
                assert page.locator('.event').count()==2
                assert page.locator('#session').inner_text().startswith('session-sha256:')
                assert 'Agent moved: research-A → research-B' in page.locator('#notice').inner_text()
                foreign,_=action('research-B',actor_headers=other_headers)
                hidden,_=action('other-agent-private',actor_headers=other_headers)
                before=hashes();page.wait_for_timeout(6200)
                healthy_snapshot_gets=get_count()-baseline
                assert healthy_snapshot_gets==0
                wire='\n'.join(observer_wire)
                assert e1['id'] in wire and e2['id'] in wire
                assert foreign['id'] not in wire and hidden['id'] not in wire and 'other-agent-private' not in wire
                assert auth['token'] not in wire and grant['code'] not in wire and s1 not in wire and s2 not in wire
                assert hashes()==before
                # Inspection and server-side filters do not change any Ember file.
                page.locator('.event').first.click()
                page.locator('input[name=namespace]').fill('research-A');page.locator('#filters button').last.click()
                wait("() => document.querySelector('#status').textContent==='LIVE' && document.querySelectorAll('.territory').length===1")
                assert page.locator('.territory').get_attribute('data-namespace')=='research-A'
                assert hashes()==before
                page.locator('#all').click();wait("() => document.querySelector('#status').textContent==='LIVE' && document.querySelectorAll('.territory').length===2")
                assert hashes()==before
                # Restart and catch-up on the same observer URL and cookie.
                stop();start();e3,r3=action('research-A',s2)
                wait('() => events.size===3',20000)
                assert page.locator('.event').count()==3
                assert page.locator('input[name=namespace]').input_value()==''
                for width,height in [(1440,900),(390,844)]:
                    page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(100)
                    if not page.evaluate('document.documentElement.scrollWidth<=innerWidth && document.documentElement.scrollHeight<=innerHeight'):
                        page.screenshot(path='/tmp/observer-overflow.png',full_page=True)
                        raise AssertionError(page.evaluate('() => ({width:innerWidth,height:innerHeight,sw:document.documentElement.scrollWidth,sh:document.documentElement.scrollHeight,overflow:[...document.querySelectorAll("body *")].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>[e.tagName,e.className,e.id,e.getBoundingClientRect().right]).slice(0,10)})'))
                page.set_viewport_size({'width':1440,'height':900})
                if args.screenshot:page.screenshot(path=args.screenshot,full_page=True)
                before=hashes()
                post('/v1/observer-access',{'action':'revoke','observer_id':grant['observer_id']})
                wait('() => territories.size===0')
                assert hashes()==before
                assert not errors,errors
                proof={'result':'PASS','observer_build':build,'same_browser_followed':['research-A','research-B','research-A'],
                       'sessions_observed':2,'namespace_entry_required':False,'first_push_seconds':round(latency,4),
                       'healthy_window_seconds':6.2,'healthy_snapshot_GETs':healthy_snapshot_gets,
                       'initial_snapshot_GETs':baseline,'cross_agent_payload_leak':False,
                       'observation_changed_memory_files':False,'restart_catchup':'PASS','revocation':'PASS',
                       'raw_initial_SSE':wire,'mutation_event_ids':[e1['id'],e2['id'],e3['id']]}
                # GET count proof was asserted before intentional filter/reconnect GETs.
                if args.proof:Path(args.proof).write_text(json.dumps(proof,indent=2))
                print(json.dumps({k:v for k,v in proof.items() if k!='raw_initial_SSE'},indent=2))
                browser.close()
        finally:stop();client.close();log.close()


if __name__=='__main__':main()
