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
                page.goto(url+'/visualizer')
                page.locator('#namespace').fill('sse-test');page.locator('#agent').fill(auth['agent_id']);page.locator('#token').fill(auth['token'])
                page.locator('#connect').click()
                wait("() => document.querySelector('#connection').textContent==='LIVE'")
                before=client.get('/v1/visualizer/sse-test',headers=headers).json()['revision']
                assert before==0
                report(1)
                wait('() => confirmed===1')
                assert page.locator('.node.pulse').count()==1
                page.locator('.node').click()
                page.evaluate('window.originalNode=document.querySelector(".node")')
                report(2)
                wait('() => confirmed===2')
                assert page.evaluate('window.originalNode===document.querySelector(".node")')
                assert page.evaluate('current.states[0].N_eff')==1
                assert len(json.loads(page.locator('#inspector').inner_text())['reports'])==2
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
                page.locator('.event').last.click()
                if args.screenshot:page.screenshot(path=args.screenshot,full_page=True)
                assert not errors,errors
                assert set(methods)=={'GET'},methods
                browser.close()
            print('PASS: push, in-place node identity, pulse, duplicate delivery, disconnect/catch-up, actual server restart, polling fallback, SSE recovery, read-only browser, no JS errors')
        finally:
            stop();client.close();log.close()


if __name__=='__main__':main()
