"""Independent direct-link/180s checks for namespace and both observer URLs.
Uses a real browser, normal server, temporary store and controllable HTTP proxy.
"""
import argparse, hashlib, json, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
from observer_transport_check import Proxy


def wait(page,expression,arg=None,timeout=20000):
    end=time.monotonic()+timeout/1000
    while time.monotonic()<end:
        if page.evaluate(expression,arg):return
        page.wait_for_timeout(50)
    raise AssertionError(page.evaluate("({error:document.querySelector('#error')?.textContent,access:document.querySelector('#accessError')?.textContent,notice:document.querySelector('#notice')?.textContent})"))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--proof',required=True);ap.add_argument('--idle-seconds',type=int,default=180);args=ap.parse_args()
    repo=Path(__file__).resolve().parents[1];proof={};proc=None
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)/'store';log=(Path(tmp)/'server.log').open('w')
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        origin=f'http://127.0.0.1:{port}';api=httpx.Client(base_url=origin,trust_env=False,timeout=10)
        def hashes():return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
        try:
            proc=subprocess.Popen([sys.executable,'-m','uvicorn','embers.api:app','--host','127.0.0.1','--port',str(port)],cwd=repo,env={**os.environ,'EMBER_STORE':str(root)},stdout=log,stderr=subprocess.STDOUT)
            for _ in range(300):
                try:api.get('/visualizer').raise_for_status();break
                except httpx.HTTPError:time.sleep(.1)
            else:raise AssertionError('Server startup timeout')
            auth=json.loads((Path(tmp)/'store-candidate-credentials.json').read_text())
            headers={'X-Ember-Agent-Id':auth['agent_id'],'X-Ember-Token':auth['token']}
            def post(path,data):
                r=api.post(path,json=data,headers=headers);r.raise_for_status();return r.json()
            rid=post('/v1/memory/write',{'namespace':'surface-A','content':{'content':'Fictional surface transport fixture'},'subject':'Surface test'})['id']
            view=post('/v1/visualizer-access',{'namespace':'surface-A','ttl_seconds':900})
            observer=post('/v1/observer-access',{'ttl_seconds':900})
            proxy=Proxy(origin);threading.Thread(target=proxy.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{proxy.server_port}'
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
                # Reproduce the removed path: withhold bytes after ready.
                old=subprocess.check_output(['git','show','dff6b7b:embers/api/visualizer.html'],cwd=repo).decode()
                legacy=browser.new_page();legacy.route('**/visualizer',lambda r:r.fulfill(body=old,content_type='text/html'))
                proxy.mode='hold';before=hashes();legacy.goto(base+'/visualizer#view='+view['code'])
                try:wait(legacy,"window.emberTransport?.connected===true",timeout=15000)
                except Exception:
                    print(legacy.evaluate("({stats:window.emberTransport,error:document.querySelector('#error')?.textContent,access:document.querySelector('#accessError')?.textContent})"));raise
                legacy.wait_for_timeout(12500);proof['before_held_chunks']=legacy.evaluate('window.emberTransport')
                assert proof['before_held_chunks']['reconnect_count']>=1
                assert proof['before_held_chunks']['snapshot_get_count']>=2
                assert hashes()==before;legacy.close();proxy.mode='pass';proxy.cut()
                pages=[];records=[];errors=[]
                paths=[('namespace','/visualizer#view='+view['code']),('observer-mode','/visualizer?mode=observer#observe='+observer['code']),('observer-alias','/observer#observe='+observer['code'])]
                before=hashes()
                for name,path in paths:
                    page=browser.new_page();requests=[];wire=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.on('request',lambda r,rows=requests:rows.append(r.url.split('#')[0]))
                    cdp=page.context.new_cdp_session(page);cdp.send('Network.enable')
                    cdp.on('Network.eventSourceMessageReceived',lambda e,rows=wire:rows.append(e))
                    page.goto(base+path)
                    selector='#connection' if name=='namespace' else '#status'
                    wait(page,'(s)=>document.querySelector(s).textContent===\'LIVE\'',arg=selector)
                    assert '#' not in page.url
                    stats='window.emberTransport' if name=='namespace' else 'window.emberObserverTransport'
                    initial=page.evaluate(stats)
                    record={'surface':name,'initial':initial,'requests':requests,'wire':wire}
                    pages.append((page,stats,selector));records.append(record)
                start=time.monotonic()
                while time.monotonic()-start<args.idle_seconds:
                    pages[0][0].wait_for_timeout(1000)
                    for page,stats,selector in pages:
                        d=page.evaluate(stats)
                        assert d['connected'] and d['reconnect_count']==0 and d['snapshot_gets']==1 and d['fallback_count']==0,d
                for (page,stats,selector),record in zip(pages,records):
                    record['after_idle']=page.evaluate(stats)
                    record['stream_requests']=sum('/v1/observer/stream?' in u or '/v1/visualizer-stream/' in u for u in record['requests'])
                    assert record['stream_requests']==1
                    scripts=page.locator('script[src]').evaluate_all('(es)=>es.map(e=>e.getAttribute("src"))')
                    record['scripts']=scripts;assert len(scripts)==1
                assert len({r['scripts'][0] for r in records})==1
                assert hashes()==before
                proof['idle_seconds']=args.idle_seconds;proof['read_only_idle']=True
                event=post('/v1/usefulness/surface-A',{'action':'report','request_id':'surfaces-feedback','payload':{'target':{'kind':'memory','memory_ids':[rid]},'context':'test','feedback_type':'CONTRIBUTED'}})
                for page,stats,selector in pages:
                    wait(page,'(s)=>window[s.replace("window.","")].pushed_events>=1',arg=stats)
                    d=page.evaluate(stats);assert d['snapshot_gets']==1 and d['reconnect_count']==0
                before=hashes()
                for page,_,_ in pages:page.close()
                for path,selector,error in [('/visualizer#view=invalid','#connection','#accessError'),('/visualizer?mode=observer#observe=invalid','#status','#accessError')]:
                    p=browser.new_page();p.goto(base+path);wait(p,'(s)=>document.querySelector(s).textContent===\'ACCESS EXPIRED\'',arg=selector)
                    assert p.locator(error).inner_text() and '#' not in p.url;p.close()
                # Query-stripped observer fragments recover through the alias.
                p=browser.new_page();p.goto(base+'/visualizer#observe='+observer['code']);wait(p,"document.querySelector('#status')?.textContent==='LIVE'");assert '/observer' in p.url;p.close()
                assert hashes()==before
                assert not errors,errors
                for record in records:
                    wire=json.dumps(record['wire']);assert auth['token'] not in wire and view['code'] not in wire and observer['code'] not in wire
                proof.update(result='PASS',surfaces=records,invalid_links='ACCESS EXPIRED',query_stripped_observer='PASS',event_id=event['id'],javascript_errors=errors)
                Path(args.proof).write_text(json.dumps(proof,indent=2)+'\n');print('PASS: all direct links, idle, shared asset, delivery, read-only')
                browser.close()
            proxy.shutdown()
        finally:
            if proc:proc.terminate();proc.wait(timeout=15)
            api.close();log.close()


if __name__=='__main__':main()
