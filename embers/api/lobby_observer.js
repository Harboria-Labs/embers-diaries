/* Read-only Lobby panel. SSE invalidates a bounded snapshot; no periodic GET. */
(()=>{
  const byId=id=>document.getElementById(id), panel=byId('lobbyView'), toggle=byId('lobbyToggle');
  let grant=null, source=null, cursor=0, retry=null, expiry=null, refresh=null, snapshot=null;
  let epoch=0, loading=false, dirty=false, active=false, failures=0, receivedAt=0;
  let stats={snapshots:0,reconnects:0,events:0}, status='NOT CONNECTED';
  const cell=(tag,value)=>{const el=document.createElement(tag);el.textContent=value==null?'Not recorded':String(value);return el};
  const when=value=>value?new Date(typeof value==='number'?value*1000:value).toLocaleString():'Not recorded';
  function diagnostics(){byId('lobbyDiagnostics').textContent=`${status} · SSE · cursor ${cursor} · snapshots ${stats.snapshots} · reconnects ${stats.reconnects} · notifications ${stats.events}`}
  const state=value=>{status=value;byId('lobbyState').textContent=value;diagnostics()};
  function render(){
    if(!snapshot)return;
    const now=(snapshot.observed_at||Date.now()/1000)+(Date.now()-receivedAt)/1000;
    const s=snapshot.scope, filters=Object.fromEntries([...panel.querySelectorAll('[data-filter]')].map(e=>[e.dataset.filter,e.value.trim().toLowerCase()]));
    byId('lobbyScope').textContent=`Task: ${s.task} · Namespace: ${s.namespace} · ${s.status} · Members (not online presence): ${s.members.join(', ')}`;
    const rows=snapshot.items.map(i=>({...i,status:i.status==='active'&&i.expires_at<=now?'expired':i.status})).filter(i=>Object.entries(filters).every(([key,value])=>!value||String(key==='task'?s.task:key==='namespace'?s.namespace:i[key]||'').toLowerCase().includes(value)));
    const list=byId('lobbyItems'), present=new Set();
    for(const item of rows){
      present.add(item.item_id);
      let card=[...list.children].find(e=>e.dataset.id===item.item_id);
      const signature=JSON.stringify(item);
      if(card?.dataset.signature===signature)continue;
      if(!card){card=document.createElement('article');card.className='lobby-card';card.dataset.id=item.item_id;list.append(card)}
      card.dataset.signature=signature;card.replaceChildren();
      card.append(cell('h3',`${item.kind} · ${item.status} · version ${item.version}`));
      card.append(cell('p',item.status==='active'?item.claim:'Content hidden: withdrawn, expired or closed'));
      card.append(cell('small',`Author: ${item.agent_id} · Item: ${item.item_id}`));
      card.append(cell('small',`Reported uncertainty: ${item.uncertainty} · This is not ELLA verification.`));
      if(item.status==='active'&&item.uncertainty_note)card.append(cell('small',item.uncertainty_note));
      card.append(cell('small',`Evidence: ${item.evidence_status||'Not recorded'} · ${item.evidence_count} references · ${item.contribution_count} contributions`));
      for(const e of item.evidence||[])card.append(cell('small',`Evidence ${e.evidence_id}: ${e.availability} · ${e.source||'Source not recorded'} · agent ${e.agent_id} · observed ${when(e.observed_at)}`));
      if(item.contributions_truncated)card.append(cell('small','Showing only the latest 16 contributor summaries.'));
      for(const c of item.contributions||[])card.append(cell('small',`${c.kind} contribution by ${c.agent_id} · ${when(c.created_at)} · ${(c.evidence||[]).map(e=>e.availability).join(', ')||'No evidence attached'}`));
      card.append(cell('small',`Created ${when(item.created_at)} · updated ${when(item.updated_at)} · expires ${when(item.expires_at)}`));
      card.append(cell('small',`Version ${item.version} is current; earlier text/history is not exposed. Version changes may include contributions or corrections.`));
      card.append(cell('small',item.handoff?`Handed off to ${item.handoff.durable_kind} ${item.handoff.durable_id} · ${when(item.handoff.accepted_at)}. Handoff does not prove promotion or truth.`:item.handoff_pending?'Durable handoff pending':'No durable handoff recorded'));
      card.classList.remove('lobby-pulse');void card.offsetWidth;card.classList.add('lobby-pulse');
    }
    for(const card of [...list.children])if(!present.has(card.dataset.id))card.remove();
    byId('lobbyNotice').textContent=rows.length?`${rows.length} of ${snapshot.items.length} recent items (limit ${snapshot.item_limit||1000}). Artifact bodies and reference URLs are withheld. Supersession and final promotion decisions are not recorded in this view.`:'No working memories match this view. Shared items will appear here live.';
    clearTimeout(expiry);
    const future=snapshot.items.filter(i=>i.status==='active'&&i.expires_at>now).map(i=>i.expires_at-now);
    if(future.length)expiry=setTimeout(render,Math.min(2147483647,Math.max(1,Math.min(...future)*1000+20)));
    diagnostics();
  }
  function closeStream(){source?.close();source=null}
  function stop(){epoch++;closeStream();clearTimeout(retry);clearTimeout(refresh);clearTimeout(expiry);loading=false;dirty=false}
  function ended(message){stop();active=false;snapshot=null;byId('lobbyItems').replaceChildren();byId('lobbyScope').textContent='';state('ACCESS ENDED');byId('lobbyNotice').textContent=message}
  function recover(error){
    closeStream();clearTimeout(retry);state('RECONNECTING');byId('lobbyNotice').textContent=error.message;
    stats.reconnects++;diagnostics();
    retry=setTimeout(()=>{retry=null;reload(true)},Math.min(15000,1500*2**Math.min(failures++,3)));
  }
  async function reload(connectAfter=false){
    if(!active)return;
    if(loading){dirty=true;return}
    const token=epoch;loading=true;dirty=false;
    try{
      stats.snapshots++;diagnostics();
      const response=await fetch('/v1/observer/lobby',{cache:'no-store'});
      if(token!==epoch)return;
      if(response.status===401||response.status===403){ended('Lobby authorization expired, was revoked, or its session ended. Request a new authorized link.');return}
      if(!response.ok)throw Error(`Lobby snapshot HTTP ${response.status}`);
      const data=await response.json();if(token!==epoch)return;
      snapshot=data;cursor=data.cursor;receivedAt=Date.now();render();failures=0;
      if(connectAfter||!source)connect();
    }catch(error){if(token===epoch)recover(error)}
    finally{if(token===epoch){loading=false;if(dirty&&!retry){dirty=false;refresh=setTimeout(()=>reload(),0)}}}
  }
  function connect(){
    if(!active||!grant?.lobby_scope_id)return;
    closeStream();clearTimeout(retry);state('RECONNECTING');
    const s=new EventSource('/v1/observer/lobby/stream?after='+cursor);source=s;
    s.addEventListener('ready',()=>{if(source===s)state('LIVE')});
    const changed=e=>{
      if(source!==s||Number(e.lastEventId)<=cursor)return;
      stats.events++;diagnostics();
      // Cursor advances only after its corresponding snapshot is rendered.
      clearTimeout(refresh);refresh=setTimeout(()=>reload(),50);
    };
    for(const name of ['working_memory_changed','working_memory_removed','scope_changed'])s.addEventListener(name,changed);
    s.addEventListener('reset',()=>{if(source!==s)return;closeStream();state('RESET / RELOADING');clearTimeout(refresh);refresh=setTimeout(()=>reload(true),1500)});
    s.addEventListener('denied',()=>{if(source===s)ended('Lobby authorization ended. Request a new authorized link.')});
    s.onerror=()=>{if(source===s)recover(Error('Lobby stream disconnected; recovering from the last rendered cursor.'))};
  }
  toggle.onclick=()=>{
    const show=panel.hidden;panel.hidden=!show;document.body.classList.toggle('lobby-active',show);byId('observerWorkspace').hidden=show;byId('filters').hidden=show;toggle.textContent=show?'Research activity':'Live Lobby';
    if(show&&grant?.lobby_scope_id){active=true;reload(true)}else{stop();active=false;state('PAUSED')}
  };
  panel.querySelectorAll('[data-filter]').forEach(input=>input.addEventListener('input',render));
  window.addEventListener('pagehide',stop);
  window.EmberLobbyObserver={open(value){stop();active=false;grant=value;cursor=0;snapshot=null;stats={snapshots:0,reconnects:0,events:0};toggle.hidden=!value.lobby_scope_id;panel.hidden=true;document.body.classList.remove('lobby-active');byId('lobbyItems').replaceChildren();byId('observerWorkspace').hidden=false;byId('filters').hidden=false;toggle.textContent='Live Lobby';state('NOT CONNECTED')}};
})();
