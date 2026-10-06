/* Observer transport only. No memory/model operations. One owner, one EventSource. */
class EmberObserverTransport {
  constructor({params, snapshot, receive, onState, onDiagnostic, onNotice, onTerminal, agent, scope={}}) {
    Object.assign(this, {params, snapshot, receive, onState, onDiagnostic, onNotice, onTerminal, agent});
    this.scope={snapshotUrl:()=>'/v1/observer/events?'+this.params(),streamUrl:()=>'/v1/observer/stream?'+this.params(),statusUrl:'/v1/observer/status',eventName:'observer',validateHello:h=>h.protocol==='ember-agent-observer.v1'&&h.observed_agent_id===this.agent,validateStatus:d=>d.observed_agent_id===this.agent,cursor:d=>d.cursor,more:d=>d.more,validateEvent:(d,id)=>d.cursor===id,...scope};
    this.stopped=false; this.source=null; this.failures=0; this.attempts=0;
    this.fallback=false; this.polling=false; this.rest=new Set();
    this.stats={transport:'NONE',connected:false,connection_state:'RECONNECTING',stream_id:null,
      last_received_event_id:null,reconnect_count:0,snapshot_gets:0,fallback_count:0,
      pushed_events:0,last_pushed:null,last_snapshot:null,last_disconnect_reason:null,
      last_http_status:null,last_sse_status:null,grant_expires_at:null,stream_attempts:0,last_byte_at:null};
  }
  emit(){this.onDiagnostic(this.stats)}
  state(value){Object.assign(this.stats,{connection_state:value,connected:value==='LIVE',
    transport:value==='LIVE'?'SSE':value==='POLLING FALLBACK'?'POLLING':'NONE'});this.onState(value,this.stats.transport);this.emit()}
  timer(name,fn,ms){clearTimeout(this[name]);this[name]=setTimeout(fn,ms)}
  async json(url){
    const c=new AbortController();this.rest.add(c);
    const timer=setTimeout(()=>c.abort('REST timeout'),10000);
    try{const r=await fetch(url,{signal:c.signal,cache:'no-store'});if(this.stopped||c.signal.aborted)throw Error('Observer request superseded');this.stats.last_http_status=r.status;this.emit();
      if(!r.ok){const e=Error('Observer HTTP '+r.status);e.denied=[401,403].includes(r.status);e.reset=r.status===400;throw e}
      const data=await r.json();if(this.stopped||c.signal.aborted)throw Error('Observer request superseded');return data;
    }finally{clearTimeout(timer);this.rest.delete(c)}
  }
  async authorize(){
    const data=await this.json(this.scope.statusUrl);
    if(!this.scope.validateStatus(data)){const e=Error('Observer target changed');e.denied=true;throw e}
    this.stats.grant_expires_at=data.expires_at;this.emit();
    // Server-supplied remaining lifetime avoids client clock skew. No grant extension.
    if(Number.isFinite(data.remaining_seconds))this.timer('expiryTimer',()=>this.checkExpiry(),Math.max(1000,data.remaining_seconds*1000));
  }
  async checkExpiry(){if(this.stopped)return;try{await this.authorize()}catch(e){if(e.denied)this.terminal('ACCESS EXPIRED','Authorization expired, revoked or unavailable');else this.timer('expiryTimer',()=>this.checkExpiry(),5000)}}
  async start(){
    this.state('RECONNECTING');
    try{await this.takeSnapshot()}catch(e){if(this.stopped)return;if(this.endIfTerminal(e))return;this.onNotice(e.message)}
    if(!this.stopped)this.connect();
  }
  async takeSnapshot(){
    let more;
    do{this.stats.snapshot_gets++;this.emit();const data=await this.json(this.scope.snapshotUrl());
      if(this.stopped)return;this.receive(data,'SNAPSHOT');this.stats.last_snapshot=this.scope.cursor(data);more=this.scope.more(data);
    }while(more&&!this.stopped);
  }
  endIfTerminal(e){if(e.denied){this.terminal('ACCESS EXPIRED','Authorization expired, revoked or unavailable');return true}
    if(e.reset){this.terminal('RECOVERY REQUIRED','Observer cursor/journal unavailable; reopen explicitly');return true}return false}
  connect(){
    if(this.stopped||this.source)return;
    // Never let a late fallback snapshot roll the live cursor backwards.
    // Cancel that REST owner before taking the resume cursor for a new stream.
    if(this.fallback)this.stopFallback();
    clearTimeout(this.retryTimer);if(this.attempts++)this.stats.reconnect_count++;
    this.stats.stream_attempts++;this.stats.last_sse_status='CONNECTING';this.emit();
    this.state('RECONNECTING');
    const es=new EventSource(this.scope.streamUrl());this.source=es;
    let ready=false,openedAt=0;
    const current=()=>!this.stopped&&this.source===es;
    const liveness=()=>{if(ready&&performance.now()-openedAt>=15000)this.failures=0;this.stats.last_byte_at=new Date().toISOString();this.timer('idleTimer',()=>{if(current())this.lost(es,'No SSE event/keepalive for 45 seconds')},45000)};
    // Connection/handshake timeout only. NEVER abort a healthy body after ten seconds.
    this.timer('connectTimer',()=>{if(current())this.lost(es,'SSE handshake timeout')},30000);
    es.onopen=()=>{if(current()){this.stats.last_sse_status=200;this.emit()}};
    es.addEventListener('ready',event=>{
      if(!current())return;
      try{const hello=JSON.parse(event.data);
        if(!this.scope.validateHello(hello))throw Error('Wrong observer handshake');
        openedAt=performance.now();ready=true;liveness();clearTimeout(this.connectTimer);this.stats.stream_id=hello.stream_id;
        this.stats.grant_expires_at=hello.expires_at;this.stopFallback();this.state('LIVE');
        if(Number.isFinite(hello.remaining_seconds))this.timer('expiryTimer',()=>this.checkExpiry(),Math.max(1000,hello.remaining_seconds*1000));
      }catch(e){this.lost(es,e.message)}
    });
    es.addEventListener(this.scope.eventName,event=>{
      if(!current())return;
      try{if(!ready)throw Error('Missing SSE handshake');const data=JSON.parse(event.data);
        if(!event.lastEventId||!this.scope.validateEvent(data,event.lastEventId))throw Error('Cursor mismatch');
        liveness();this.receive(data,'SSE',event.lastEventId);this.stats.last_received_event_id=event.lastEventId;
        this.stats.last_pushed=event.lastEventId;this.stats.pushed_events+=data.events.length;this.emit();
      }catch(e){this.lost(es,e.message)}
    });
    es.addEventListener('keepalive',event=>{if(current()&&ready){try{if(JSON.parse(event.data).stream_id===this.stats.stream_id)liveness()}catch{}}});
    es.addEventListener('denied',()=>{if(current())this.terminal('ACCESS EXPIRED','Authorization expired, revoked or unavailable')});
    es.addEventListener('reset',()=>{if(current())this.terminal('RECOVERY REQUIRED','Observer cursor/journal unavailable; reopen explicitly')});
    es.onerror=()=>{if(current())this.lost(es,'EventSource network error (HTTP status not exposed by browser)')};
  }
  closeSource(){const es=this.source;this.source=null;if(es)es.close();clearTimeout(this.connectTimer);clearTimeout(this.stableTimer);clearTimeout(this.idleTimer)}
  async lost(es,reason){
    if(this.stopped||this.source!==es)return;
    // Disable native auto-retry before scheduling our single controlled reconnect.
    this.closeSource();this.stats.last_disconnect_reason=reason;this.stats.last_sse_status='DISCONNECTED';this.failures++;
    this.state(this.fallback?'POLLING FALLBACK':'RECONNECTING');this.onNotice(reason);
    try{await this.authorize()}catch(e){if(this.stopped)return;if(this.endIfTerminal(e))return}
    if(this.stopped)return;
    if(this.failures>=3)this.beginFallback();
    const delay=this.fallback?10000:Math.min(1000*2**(this.failures-1),8000);
    this.timer('retryTimer',()=>this.connect(),delay);
  }
  beginFallback(){if(this.fallback||this.stopped)return;this.fallback=true;this.stats.fallback_count++;this.state('POLLING FALLBACK');this.poll()}
  async poll(){
    if(!this.fallback||this.stopped||this.polling)return;this.polling=true;
    try{await this.takeSnapshot()}catch(e){if(!this.stopped&&!this.endIfTerminal(e))this.onNotice(e.message)}
    finally{this.polling=false;if(this.fallback&&!this.stopped)this.timer('pollTimer',()=>this.poll(),5000)}
  }
  stopFallback(){this.fallback=false;clearTimeout(this.pollTimer);
    // Finish/cancel a snapshot before it can overwrite a newer stream cursor.
    for(const c of this.rest)c.abort('SSE established');
  }
  terminal(state,reason){this.stop();this.stats.last_disconnect_reason=reason;this.state(state);this.onNotice(reason);this.onTerminal(state)}
  stop(){this.stopped=true;this.closeSource();for(const name of ['retryTimer','pollTimer','expiryTimer'])clearTimeout(this[name]);for(const c of this.rest)c.abort('Observer stopped');this.rest.clear();this.fallback=false}
}
window.EmberObserverTransport=EmberObserverTransport;
