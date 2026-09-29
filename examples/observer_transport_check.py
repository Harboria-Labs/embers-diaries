"""Real browser -> controlled HTTP proxy -> normal Uvicorn/ASGI observer.
Only fictional agent clients mutate Ember. Transport controls never call its models.
Run: python examples/observer_transport_check.py --idle-seconds 180 --proof PATH
"""
import argparse,hashlib,json,os,socket,subprocess,sys,tempfile,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright

class Proxy(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,upstream):
        super().__init__(('127.0.0.1',0),Handler);self.upstream=upstream;self.mode='pass';self.sockets=set();self.lock=threading.Lock();self.streams=0;self.raw=[]
    def cut(self):
        with self.lock: sockets=list(self.sockets)
        for s in sockets:
            try:s.shutdown(socket.SHUT_RDWR)
            except OSError:pass
class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):self.forward()
    def do_POST(self):self.forward()
    def forward(self):
        stream=self.path.startswith('/v1/observer/stream');proxy=self.server
        if stream and proxy.mode=='down':
            self.send_response(503);self.send_header('Content-Length','0');self.end_headers();return
        if stream:
            with proxy.lock:proxy.sockets.add(self.connection);proxy.streams+=1
        try:
            with httpx.Client(base_url=proxy.upstream,trust_env=False,timeout=None) as client:
                headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','connection','accept-encoding')}
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                with client.stream(self.command,self.path,headers=headers,content=body) as r:
                    self.send_response(r.status_code)
                    for k,v in r.headers.items():
                        if k not in ('content-length','transfer-encoding','connection'):self.send_header(k,v)
                    self.send_header('Transfer-Encoding','chunked');self.end_headers();started=time.monotonic();first=True
                    for chunk in r.iter_raw():
                        if stream:
                            proxy.raw.append(chunk.decode('utf8','replace'))
                            # Simulate a proxy withholding later heartbeats, but not closing upstream.
                            if proxy.mode=='hold' and not first:
                                while proxy.mode=='hold':time.sleep(.05)
                            first=False
                        self.wfile.write(f'{len(chunk):x}\r\n'.encode()+chunk+b'\r\n');self.wfile.flush()
                        if stream and proxy.mode=='flap':self.close_connection=True;return
                    self.wfile.write(b'0\r\n\r\n');self.wfile.flush()
        except (OSError,httpx.HTTPError):self.close_connection=True
        finally:
            if stream:
                with proxy.lock:proxy.sockets.discard(self.connection)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--idle-seconds',type=int,default=180);parser.add_argument('--proof',required=True);parser.add_argument('--before-html');parser.add_argument('--flap-only',action='store_true');args=parser.parse_args()
    proof={};repo=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='ember-transport-') as tmp:
        root=Path(tmp)/'store';logpath=Path(tmp)/'server.log';log=logpath.open('w');sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close();origin=f'http://127.0.0.1:{port}'
        server=None
        def start():
            nonlocal server
            server=subprocess.Popen([sys.executable,'-m','uvicorn','embers.api:app','--host','127.0.0.1','--port',str(port),'--timeout-graceful-shutdown','1'],cwd=repo,env={**os.environ,'EMBER_STORE':str(root)},stdout=log,stderr=subprocess.STDOUT)
            for _ in range(150):
                try:
                    if httpx.get(origin+'/health',trust_env=False).status_code==200:return
                except httpx.HTTPError:pass
                time.sleep(.1)
            raise RuntimeError('Normal server unavailable')
        def stop():
            if server and server.poll() is None:server.terminate();server.wait(10)
        start();proxy=Proxy(origin);threading.Thread(target=proxy.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{proxy.server_port}'
        client=httpx.Client(base_url=origin,trust_env=False,timeout=15)
        try:
            auth=json.loads((Path(tmp)/'store-candidate-credentials.json').read_text());headers={'X-Ember-Agent-Id':auth['agent_id'],'X-Ember-Token':auth['token']}
            def post(path,body,h=None):
                r=client.post(path,headers=h or headers,json=body);r.raise_for_status();return r.json()
            def hashes():return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            memories={ns:post('/v1/memory/write',{'namespace':ns,'content':'Fictional transport fixture','primary_context':'transport'})['id'] for ns in ['A','B','C']}
            def event(ns='A',h=None):return post('/v1/usefulness/'+ns,{'action':'report','request_id':str(time.time_ns()),'payload':{'target':{'kind':'memory','memory_ids':[memories[ns]]},'context':'transport','feedback_type':'UNUSED'}},h)
            other=post('/v1/agents/register',{'name':'transport-foreign'});otherh={'X-Ember-Agent-Id':other['agent_id'],'X-Ember-Token':other['token']}
            grant=post('/v1/observer-access',{'namespaces':['A','B'],'ttl_seconds':900})
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True);context=browser.new_context();page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));cdp=context.new_cdp_session(page);cdp.send('Network.enable');wire=[];requests=[];sse_headers=[]
                cdp.on('Network.eventSourceMessageReceived',lambda e:wire.append({k:e[k] for k in ('eventName','eventId','data','timestamp')}))
                cdp.on('Network.requestWillBeSent',lambda e:requests.append({'url':e['request']['url'],'cursor':e['request']['headers'].get('Last-Event-ID')}))
                cdp.on('Network.responseReceived',lambda e:sse_headers.append(e['response']['headers']) if '/v1/observer/stream?' in e['response']['url'] else None)
                def wait(expr,timeout=20):
                    end=time.monotonic()+timeout
                    while time.monotonic()<end:
                        if page.evaluate(expr):return
                        page.wait_for_timeout(50)
                    raise AssertionError({'waiting':expr,'notice':page.locator('#notice').inner_text(),'stats':page.evaluate('emberObserverTransport')})
                if args.before_html:
                    page.route('**/visualizer?mode=observer',lambda route:route.fulfill(body=Path(args.before_html).read_text(),content_type='text/html'))
                    page.add_init_script('''window.abortCalls=[];const abort0=AbortController.prototype.abort;AbortController.prototype.abort=function(reason){abortCalls.push({at:performance.now(),reason:String(reason),stack:new Error().stack});return abort0.call(this,reason)}''')
                    proxy.mode='hold';page.goto(url+grant['viewer_path']);wait("document.querySelector('#status').textContent==='LIVE'")
                    page.wait_for_timeout(12500);proof={'phase':'before','proxy':'initial SSE delivered; subsequent chunks held, upstream remains open','stats':page.evaluate('emberObserverTransport'),'browser_abort_calls':page.evaluate('abortCalls'),'snapshot_requests':sum('/v1/observer/events?' in x['url'] for x in requests)}
                    proxy.mode='pass';page.close();Path(args.proof).write_text(json.dumps(proof,indent=2));print(json.dumps(proof));return
                page.goto(url+grant['viewer_path']);wait("emberObserverTransport.connected")
                wait("emberObserverTransport.stream_id!==null")
                initial=page.evaluate('({...emberObserverTransport})');before=hashes()
                if args.flap_only:
                    proxy.mode='flap';proxy.cut();wait("document.querySelector('#status').textContent==='POLLING FALLBACK'",35)
                    flap=page.evaluate('({...emberObserverTransport})');assert flap['reconnect_count']>=2
                    proxy.mode='pass';wait('emberObserverTransport.connected',20)
                    page.wait_for_timeout(16000);n=page.evaluate('emberObserverTransport.reconnect_count')
                    proxy.mode='hold';wait("document.querySelector('#status').textContent==='RECONNECTING'",55)
                    assert '45 seconds' in page.evaluate('emberObserverTransport.last_disconnect_reason')
                    proxy.mode='pass';wait('emberObserverTransport.connected',20)
                    assert page.evaluate('emberObserverTransport.reconnect_count')==n+1
                    assert hashes()==before
                    proof={'result':'PASS','rapid_ready_disconnect_fallback':flap,'half_open_detection_seconds':45,'recovered':page.evaluate('({...emberObserverTransport})'),'memory_unchanged':True}
                    Path(args.proof).write_text(json.dumps(proof,indent=2));print(json.dumps(proof),flush=True);return
                # Several-minute idle test, no production clocks/model steps invoked.
                for elapsed in range(0,args.idle_seconds,30):
                    page.wait_for_timeout(min(30,args.idle_seconds-elapsed)*1000)
                    assert page.evaluate('emberObserverTransport.connected && emberObserverTransport.reconnect_count===0')
                    print(json.dumps({'idle_seconds':min(elapsed+30,args.idle_seconds),'state':'LIVE'}),flush=True)
                assert hashes()==before
                idle=page.evaluate('({...emberObserverTransport})');assert idle['snapshot_gets']==1 and idle['stream_attempts']==1
                actual_sse_requests=sum('/v1/observer/stream?' in r['url'] for r in requests)
                assert actual_sse_requests==1
                proof['healthy_idle']={'seconds':args.idle_seconds,'initial':initial,'after':idle,'sse_network_requests':actual_sse_requests,'memory_unchanged':True}
                delivered=[event('A') for _ in range(5)]+[event('B') for _ in range(25)]
                wait('events.size===30');assert page.locator('.event').count()==30
                stream_events=lambda:[v for x in wire if x['eventName']=='observer' for v in json.loads(x['data'])['events']]
                ids=[x['event_id'] for x in stream_events()];assert len(ids)==len(set(ids))==30
                for ns in ['A','B']:
                    revs=[x['revision'] for x in stream_events() if x['namespace']==ns];assert revs==sorted(set(revs))
                assert page.evaluate('emberObserverTransport.reconnect_count===0 && emberObserverTransport.snapshot_gets===1')
                hidden=[event('A',otherh),event('C')];page.wait_for_timeout(1500);raw=json.dumps(wire);assert all(e['id'] not in raw for e in hidden)
                print('Burst and raw isolation PASS',flush=True)
                proof['events_burst']={'count':30,'ordered_per_namespace':True,'duplicates':0,'cross_agent_and_namespace_leaks':False}
                # Actual socket interruption; server continues writing to durable journal.
                proxy.mode='down';proxy.cut();wait("document.querySelector('#status').textContent==='RECONNECTING'")
                cursor=page.evaluate('cursor');missed=[event('A') for _ in range(3)];proxy.mode='pass'
                wait('events.size===33 && emberObserverTransport.connected');assert page.locator('.event').count()==33
                assert page.evaluate('emberObserverTransport.snapshot_gets')==1
                assert any('cursor='+__import__('urllib.parse',fromlist=['quote']).quote(cursor,safe='') in x['url'] for x in requests)
                print('Interruption/cursor replay PASS',flush=True)
                proof['interruption']={'replayed_ids':[x['id'] for x in missed],'resume_cursor':cursor,'snapshots':1,'same_page':True}
                page.wait_for_timeout(15500)
                # Proxy stalls heartbeat delivery longer than the removed ten-second body deadline.
                count=page.evaluate('emberObserverTransport.reconnect_count');proxy.mode='hold';page.wait_for_timeout(12500)
                assert page.evaluate('emberObserverTransport.connected') and page.evaluate('emberObserverTransport.reconnect_count')==count
                proxy.mode='pass';proof['delayed_heartbeat']={'seconds':12.5,'client_aborts':0}
                # Real normal-server restart, same browser and grant. No refresh.
                stop();wait("document.querySelector('#status').textContent==='RECONNECTING'",60);start();restart_event=event('B');wait('events.size===34 && emberObserverTransport.connected',30)
                assert page.evaluate('emberObserverTransport.snapshot_gets')==1
                print('Same-page restart PASS',flush=True)
                proof['restart']={'same_page':True,'replayed_id':restart_event['id'],'snapshot_gets':1}
                page.wait_for_timeout(15500)
                proxy.mode='down';proxy.cut();wait("document.querySelector('#status').textContent==='POLLING FALLBACK'",40)
                n=page.evaluate('emberObserverTransport.snapshot_gets');fallback_event=event();wait('events.size===35');page.wait_for_timeout(10500)
                delta=page.evaluate('emberObserverTransport.snapshot_gets')-n;assert 1<=delta<=4
                proxy.mode='pass';wait('emberObserverTransport.connected',20);n=page.evaluate('emberObserverTransport.snapshot_gets');page.wait_for_timeout(16000);assert page.evaluate('emberObserverTransport.snapshot_gets')==n
                print('Fallback and recovery PASS',flush=True)
                proof['fallback']={'failure_threshold':3,'poll_interval_seconds':5,'snapshots_in_window':delta,'recovered':True,'healthy_snapshot_growth':0}
                raw=json.dumps(wire);assert all(e['id'] not in raw for e in hidden)
                ids=[x['event_id'] for x in stream_events()];assert len(ids)==len(set(ids))
                before=hashes();page.wait_for_timeout(2200);assert hashes()==before
                proof['final_counters']=page.evaluate('({...emberObserverTransport})')
                post('/v1/observer-access',{'action':'revoke','observer_id':grant['observer_id']});wait("document.querySelector('#status').textContent==='ACCESS EXPIRED'")
                attempts=page.evaluate('emberObserverTransport.stream_attempts');page.wait_for_timeout(11000);assert page.evaluate('emberObserverTransport.stream_attempts')==attempts;assert hashes()==before
                proof['revoked']={'terminal':'ACCESS EXPIRED','retry_growth':0,'memory_unchanged':True}
                exp=post('/v1/observer-access',{'ttl_seconds':60});page.goto(url+exp['viewer_path']);wait('emberObserverTransport.connected');before=hashes()
                wait("document.querySelector('#status').textContent==='ACCESS EXPIRED'",70);attempts=page.evaluate('emberObserverTransport.stream_attempts');page.wait_for_timeout(3000);assert page.evaluate('emberObserverTransport.stream_attempts')==attempts;assert hashes()==before
                proof['expired']={'terminal':'ACCESS EXPIRED','retry_growth':0,'memory_unchanged':True}
                assert not errors,errors
                assert auth['token'] not in raw and grant['code'] not in raw and exp['code'] not in raw
                proof.update(result='PASS',raw_sse_headers=sse_headers,raw_browser_sse=wire,javascript_errors=errors,unauthorized_payloads=0)
                Path(args.proof).write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps({k:v for k,v in proof.items() if k not in ('raw_browser_sse','raw_sse_headers')}),flush=True)
                browser.close()
        finally:
            proxy.mode='pass';proxy.cut();proxy.shutdown();stop();client.close();log.close()
            Path(args.proof+'.server.log').write_text(logpath.read_text())
            if not Path(args.proof).exists():Path(args.proof).write_text(json.dumps({**proof,'result':'INCOMPLETE'},indent=2))
if __name__=='__main__':main()
