/* Read-only Lobby panel inside the existing Observatory. No Lobby write route. */
(()=>{
  const byId=id=>document.getElementById(id);
  const panel=byId('lobbyView'), toggle=byId('lobbyToggle');
  let grant=null, source=null, cursor=0, reconnect=null, refresh=null, snapshot=null;
  const state=value=>{byId('lobbyState').textContent=value};
  const cell=(tag,value)=>{const el=document.createElement(tag);el.textContent=value==null?'—':String(value);return el};
  const when=value=>value?new Date(value*1000).toLocaleString():'—';
  function render(){
    if(!snapshot)return;
    const s=snapshot.scope, filters=Object.fromEntries([...panel.querySelectorAll('[data-filter]')].map(e=>[e.dataset.filter,e.value.trim().toLowerCase()]));
    byId('lobbyScope').textContent=`${s.namespace} / ${s.task} · ${s.status} · participants: ${s.members.join(', ')} · created ${when(s.created_at)}`;
    const list=byId('lobbyItems');list.replaceChildren();
    const rows=snapshot.items.filter(i=>Object.entries(filters).every(([key,value])=>!value||String(key==='task'?s.task:key==='namespace'?s.namespace:i[key]||'').toLowerCase().includes(value)));
    for(const item of rows){
      const card=document.createElement('article');card.className='lobby-card';
      card.append(cell('h3',`${item.kind} · ${item.status} · v${item.version}`));
      card.append(cell('p',item.claim||'Content unavailable after removal'));
      card.append(cell('small',`Agent ${item.agent_id} · uncertainty ${item.uncertainty} · evidence ${item.evidence_count} · peer contributions ${item.contribution_count}`));
      card.append(cell('small',`Created ${when(item.created_at)} · updated ${when(item.updated_at)} · expires ${when(item.expires_at)}`));
      card.append(cell('small',item.handoff?`Durable ${item.handoff.durable_kind} ${item.handoff.durable_id} · accepted ${when(item.handoff.accepted_at)}`:item.handoff_pending?'Durable handoff pending':'No durable handoff'));
      list.append(card);
    }
    byId('lobbyNotice').textContent=`${rows.length} of ${snapshot.items.length} recent provisional memories · event cursor ${cursor} · evidence content is never shown here.`;
  }
  async function load(){
    const response=await fetch('/v1/observer/lobby',{cache:'no-store'});
    if(!response.ok)throw Error(response.status===403?'Lobby access expired or revoked':`Lobby snapshot HTTP ${response.status}`);
    snapshot=await response.json();cursor=Math.max(cursor,snapshot.cursor);render();
  }
  function stop(){source?.close();source=null;clearTimeout(reconnect);clearTimeout(refresh)}
  function connect(){
    if(!grant?.lobby_scope_id)return;
    source?.close();state('RECONNECTING');
    const s=new EventSource('/v1/observer/lobby/stream?after='+cursor);source=s;
    s.addEventListener('ready',()=>{if(source===s)state('LIVE')});
    const changed=e=>{if(source!==s)return;cursor=Math.max(cursor,Number(e.lastEventId)||0);clearTimeout(refresh);refresh=setTimeout(()=>load().catch(fail),50)};
    for(const name of ['working_memory_changed','working_memory_removed','scope_changed'])s.addEventListener(name,changed);
    s.addEventListener('reset',()=>{if(source!==s)return;state('RESET / RELOADING');s.close();source=null;cursor=0;load().then(connect).catch(fail)});
    s.addEventListener('denied',()=>{if(source!==s)return;stop();state('ACCESS ENDED');byId('lobbyItems').replaceChildren()});
    s.onerror=()=>{if(source!==s)return;s.close();source=null;state('RECONNECTING');reconnect=setTimeout(connect,1500)};
  }
  function fail(error){stop();state('ACCESS / RECOVERY REQUIRED');byId('lobbyNotice').textContent=error.message;byId('lobbyItems').replaceChildren()}
  toggle.onclick=()=>{const show=panel.hidden;panel.hidden=!show;byId('observerWorkspace').hidden=show;byId('filters').hidden=show;toggle.textContent=show?'Research activity':'Live Lobby';if(show&&grant?.lobby_scope_id&&!source)load().then(connect).catch(fail)};
  panel.querySelectorAll('[data-filter]').forEach(input=>input.addEventListener('input',render));
  window.addEventListener('pagehide',stop);
  window.EmberLobbyObserver={open(value){stop();grant=value;cursor=0;snapshot=null;toggle.hidden=!value.lobby_scope_id;panel.hidden=true;byId('observerWorkspace').hidden=false;byId('filters').hidden=false;toggle.textContent='Live Lobby';state('NOT CONNECTED')}};
})();
