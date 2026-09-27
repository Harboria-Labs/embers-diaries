"""Isolated normal-server/browser SSE checks. Requires httpx and Playwright Chromium.

Starts only a temporary local server/store; never writes to an existing user store.
"""
import argparse
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
    parser.add_argument('--screenshot')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ember-sse-') as tmp:
        root=Path(tmp)/'store'
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        url=f'http://127.0.0.1:{port}'
        client=httpx.Client(base_url=url,trust_env=False,timeout=10)
        log=open(Path(tmp)/'server.log','w')
        process=None
        def start():
            nonlocal process
            process=subprocess.Popen([sys.executable,'-m','uvicorn','embers.api:app','--host','127.0.0.1','--port',str(port),'--timeout-graceful-shutdown','1'],
                env={**os.environ,'EMBER_STORE':str(root)},stdout=log,stderr=subprocess.STDOUT)
            for _ in range(100):
                try:client.get('/visualizer').raise_for_status();return
                except httpx.HTTPError:time.sleep(.1)
            raise RuntimeError('server did not start')
        def stop():
            if process and process.poll() is None:process.terminate();process.wait(timeout=10)
        try:
            start()
            auth=json.loads((Path(tmp)/'store-candidate-credentials.json').read_text())
            headers={'X-Ember-Agent-Id':auth['agent_id'],'X-Ember-Token':auth['token']}
            def post(path,body):
                result=client.post(path,json=body,headers=headers);result.raise_for_status();return result.json()
            rid=post('/v1/memory/write',{'namespace':'sse-test','content':{'content':'fictional latent memory','verify_status':'hypothesis'},'subject':'LADC','primary_context':'research'})['id']
            def report(n):
                return post('/v1/usefulness/sse-test',{'action':'report','request_id':f'report-{n}',
                    'payload':{'target':{'kind':'memory','memory_ids':[rid]},'context':'research','feedback_type':'CONTRIBUTED',
                               'identity':{'value':'one-outcome','source':'test','provenance':'synthetic trusted fixture'},'identity_verified':True}})
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1000})
                def wait(predicate,timeout=15000):
                    deadline=time.monotonic()+timeout/1000
                    while time.monotonic()<deadline:
                        if page.evaluate(predicate):return
                        page.wait_for_timeout(100)
                    raise AssertionError(page.evaluate('() => ({confirmed,status:document.querySelector("#connection").textContent,error:document.querySelector("#error").textContent})'))
                errors=[];methods=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:methods.append(r.method))
                grant=post('/v1/visualizer-access',{'namespace':'sse-test'})
                page.goto(url+grant['viewer_path'])
                wait("() => document.querySelector('#connection').textContent==='LIVE'")
                before=client.get('/v1/visualizer/sse-test',headers=headers).json()['revision']
                assert before==0
                report(1)
                wait('() => confirmed===1')
                assert page.evaluate('id => pulses.get(id)>performance.now()',rid)
                page.evaluate('id => selectNode(id)',rid)
                page.evaluate('id => window.originalNode=scene.get(id)',rid)
                report(2)
                wait('() => confirmed===2')
                assert page.evaluate('id => window.originalNode===scene.get(id)',rid)
                assert page.evaluate('current.states[0].N_eff')==1
                assert page.evaluate('current.reports.length')==2
                # Applying an already-confirmed frame again is a no-op.
                page.evaluate('applyPatch({},2)')
                assert page.locator('.event').count()==2
                # Drop browser connection while server commits a missed event.
                page.evaluate('controller.abort()')
                report(3)
                wait('() => confirmed===3',timeout=15000)
                # Real process restart; persisted revision/state recover automatically.
                stop();start();report(4)
                wait('() => confirmed===4',timeout=20000)
                # Block SSE to force authenticated snapshot fallback.
                page.route('**/v1/visualizer-stream/**',lambda route:route.fulfill(status=200,content_type='text/html',body='proxy blocked SSE'))
                page.evaluate('controller.abort()')
                report(5)
                wait('() => confirmed===5',timeout=20000)
                wait("() => document.querySelector('#connection').textContent==='POLLING FALLBACK'",timeout=20000)
                page.unroute('**/v1/visualizer-stream/**')
                wait("() => document.querySelector('#connection').textContent==='LIVE'",timeout=20000)
                state=client.get('/v1/visualizer/sse-test',headers=headers).json()
                assert state['revision']==5 and state['states'][0]['N_eff']==1
                page.wait_for_timeout(1200)
                assert client.get('/v1/visualizer/sse-test',headers=headers).json()['revision']==5
                # Real persisted synthetic research records, not injected UI data.
                ids=[rid]
                for i in range(1,25):
                    topic=['LADC','Pairing Matrix','Feedback','Context'][i%4]
                    context=['research','mathematics','experiments'][i%3]
                    mid=post('/v1/memory/write',{'namespace':'sse-test','content':{
                        'content':f'Synthetic research fixture {i}: {topic} in {context}. Distinct outcomes carry evidence; repeated exposure does not establish truth.',
                        'verify_status':'hypothesis'},'subject':topic,'primary_context':context})['id']
                    ids.append(mid)
                    post('/v1/usefulness/sse-test',{'action':'report','request_id':f'fixture-{i}',
                        'payload':{'target':{'kind':'memory','memory_ids':[mid]},'context':context,
                        'feedback_type':'MISLEADING' if i%5==0 else 'CONTRIBUTED',
                        'identity':{'value':f'fixture-outcome-{i}','source':'test','provenance':'synthetic fixture'},'identity_verified':True}})
                    post('/v1/usefulness/sse-test',{'action':'report','request_id':f'pair-{i}',
                        'payload':{'target':{'kind':'pair','memory_ids':[ids[i//2],mid],'relation':'explains'},
                        'context':context,'feedback_type':'PAIR_HELPED'}})
                post('/v1/usefulness/sse-test',{'action':'report','request_id':'group-fixture',
                    'payload':{'target':{'kind':'group','memory_ids':ids[:3]},'context':'research','feedback_type':'GROUP_SUCCESS'}})
                wait('() => confirmed===54')
                assert page.evaluate('scene.size')==25
                # Exercise all views at desktop, laptop, and phone viewport sizes.
                for width,height in [(1440,900),(1280,720),(390,844)]:
                    page.set_viewport_size({'width':width,'height':height})
                    for mode in ['network','orbit','bubbles','heatmap']:
                        page.locator('[data-view="'+mode+'"]').click()
                        page.wait_for_timeout(200)
                        if mode in ['network','orbit']:
                            assert page.evaluate('screenNodes.every(n=>n.x>=0 && n.x<=$canvas.clientWidth && n.y>=0 && n.y<=$canvas.clientHeight)')
                        if not page.evaluate('document.documentElement.scrollWidth<=innerWidth && document.documentElement.scrollHeight<=innerHeight'):
                            page.screenshot(path='/tmp/ember-overflow.png',full_page=True)
                            raise AssertionError(page.evaluate('() => ({width:innerWidth,height:innerHeight,sw:document.documentElement.scrollWidth,sh:document.documentElement.scrollHeight,overflow:[...document.querySelectorAll("body *")].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>[e.tagName,e.className,e.id,e.getBoundingClientRect().right]).slice(0,15)})'))
                page.set_viewport_size({'width':1440,'height':900})
                page.locator('[data-view="orbit"]').click()
                page.evaluate('id => selectNode(id)',rid)
                page.locator('[data-tab="tokens"]').click()
                wait("() => document.querySelectorAll('.token').length>0")
                if args.screenshot:
                    page.screenshot(path=args.screenshot,full_page=True)
                    page.locator('[data-view="heatmap"]').click()
                    page.screenshot(path=str(Path(args.screenshot).with_name('observatory-heatmap.png')),full_page=True)
                    page.locator('[data-view="bubbles"]').click()
                    page.screenshot(path=str(Path(args.screenshot).with_name('observatory-bubbles.png')),full_page=True)
                    page.locator('[data-view="network"]').click()
                    page.screenshot(path=str(Path(args.screenshot).with_name('observatory-network.png')),full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    page.wait_for_timeout(200)
                    page.screenshot(path=str(Path(args.screenshot).with_name('observatory-phone.png')),full_page=True)
                    page.set_viewport_size({'width':1440,'height':900})
                post('/v1/visualizer-access',{'action':'revoke','grant_id':grant['grant_id']})
                wait('() => current===null')
                assert page.evaluate('scene.size')==0
                assert page.locator('#inspectorTitle').inner_text()=='Memory inspector'
                # An agent can issue a replacement; manual code entry also reconnects.
                replacement=post('/v1/visualizer-access',{'namespace':'sse-test'})
                page.locator('#accessButton').click()
                page.locator('#viewCode').fill(replacement['code'])
                page.locator('#openCode').click()
                wait("() => document.querySelector('#connection').textContent==='LIVE'")
                assert not errors,errors
                assert set(methods)=={'GET','POST'},methods
                assert page.locator('#agent').input_value()==''
                assert page.locator('#token').input_value()==''
                assert page.evaluate('location.hash')==''
                browser.close()
            print('PASS: push, in-place node identity, pulse, duplicate delivery, disconnect/catch-up, actual server restart, polling fallback, SSE recovery, read-only browser, no JS errors')
        finally:
            stop();client.close();log.close()


if __name__=='__main__':main()
