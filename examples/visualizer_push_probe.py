"""Read-only independent SSE probe. Credentials are read from EMBER_VIEW_CODE.

An agent should write a new observable event while this probe is connected.
After its one initial snapshot this program never polls the snapshot endpoint.
"""
import argparse
import json
import os
import time
import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True,help='Normal Ember server origin')
    parser.add_argument('--seconds',type=int,default=60)
    args=parser.parse_args()
    if not 1<=args.seconds<=3600:parser.error('--seconds must be 1..3600')
    code=os.environ.get('EMBER_VIEW_CODE')
    if not code:parser.error('Set EMBER_VIEW_CODE to a temporary read-only viewing code')
    with httpx.Client(base_url=args.url.rstrip('/'),trust_env=False,timeout=12,
                      headers={'ngrok-skip-browser-warning':'true'}) as client:
        grant=client.post('/v1/visualizer-access/exchange',json={'code':code})
        grant.raise_for_status()
        namespace=grant.json()['namespace']
        from urllib.parse import quote
        encoded=quote(namespace,safe='')
        snapshot=client.get('/v1/visualizer/'+encoded,params={'limit':1})
        snapshot.raise_for_status()
        last=snapshot.json()['revision']
        print(json.dumps({'transport':'SNAPSHOT','last_snapshot_revision':last,'snapshot_GETs':1}),flush=True)
        end=time.monotonic()+args.seconds
        reconnects=0;pushed=0
        while time.monotonic()<end:
            try:
                with client.stream('GET','/v1/visualizer-stream/'+encoded,
                                   headers={'Last-Event-ID':str(last)},params={'after':last}) as response:
                    response.raise_for_status()
                    if not response.headers.get('content-type','').startswith('text/event-stream'):
                        raise RuntimeError('Expected text/event-stream')
                    event_type='';event_id=None;data=[];ready=False
                    for line in response.iter_lines():
                        if time.monotonic()>=end:break
                        if line:
                            if line.startswith('event:'):event_type=line[6:].strip()
                            elif line.startswith('id:'):event_id=int(line[3:].strip())
                            elif line.startswith('data:'):data.append(line[5:].lstrip())
                            continue
                        if event_type=='ready':
                            hello=json.loads('\n'.join(data))
                            if hello.get('protocol')!='ember-observation.v2':raise RuntimeError('Unexpected protocol')
                            ready=True
                            print(json.dumps({'transport':'SSE','connected':True,'reconnect_count':reconnects}),flush=True)
                        elif event_type=='patch':
                            if not ready:raise RuntimeError('Missing ready handshake: update server and viewer together')
                            payload=json.loads('\n'.join(data))
                            if event_id>last:
                                events=payload.get('events',[])
                                if [e['revision'] for e in events]!=list(range(last+1,event_id+1)):
                                    raise RuntimeError('Noncontiguous SSE revisions')
                                for event in events:
                                    print(json.dumps({'transport':'SSE','revision':event['revision'],
                                                      'event_id':event['id'],'received_at':time.time()}),flush=True)
                                pushed+=len(events);last=event_id
                        elif event_type in ('denied','reset'):
                            raise RuntimeError('Stream ended: '+event_type)
                        event_type='';event_id=None;data=[]
                if time.monotonic()<end:raise httpx.ReadError('Stream closed')
            except httpx.TransportError as error:
                reconnects+=1
                print(json.dumps({'connected':False,'state':'RECONNECTING','error_type':type(error).__name__,
                                  'last_confirmed_revision':last}),flush=True)
                time.sleep(min(1,max(0,end-time.monotonic())))
        print(json.dumps({'last_confirmed_revision':last,'pushed_events_received':pushed,
                          'snapshot_GETs':1,'reconnect_count':reconnects,
                          'result':'PUSH OBSERVED' if pushed else 'NO NEW PUSH OBSERVED'}),flush=True)


if __name__=='__main__':main()
