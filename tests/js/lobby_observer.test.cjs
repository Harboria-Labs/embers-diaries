// DOM/state-machine unit tests, not browser/layout validation.
const {test}=require('node:test');const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('embers/api/lobby_observer.js','utf8');
function fixture(){
 let now=1000000,next=0;const timers=new Map(),requests=[],streams=[],responses=[];
 class Element{
  constructor(){this.children=[];this.dataset={};this.hidden=false;this.value='';this.textContent='';this.listeners={};this.classList={add(){},remove(){}}}
  append(e){e.parent=this;this.children.push(e)}
  replaceChildren(...es){this.children=[];for(const e of es)this.append(e)}
  remove(){this.parent.children=this.parent.children.filter(e=>e!==this)}
  addEventListener(n,fn){this.listeners[n]=fn}
  querySelectorAll(){return filters}
 }
 const filters=['agent_id','kind','status'].map(key=>{let e=new Element();e.dataset.filter=key;return e});
 const elements=Object.fromEntries(['lobbyView','lobbyToggle','lobbyState','lobbyScope','lobbyItems','lobbyNotice','lobbyDiagnostics','observerWorkspace','filters'].map(k=>[k,new Element()]));
 class EventSource{
  constructor(url){this.url=url;this.handlers={};streams.push(this)}
  addEventListener(k,fn){this.handlers[k]=fn}close(){this.closed=true}
  emit(k,id){this.handlers[k]?.({lastEventId:String(id)})}
 }
 const snapshot=(cursor=1,items=[])=>({scope:{namespace:'fixture',task:'Test',status:'open',members:['a']},items,cursor,observed_at:now/1000,item_limit:1000});
 responses.push({status:200,data:snapshot()});
 const window={addEventListener(){}};
 vm.runInNewContext(source,{document:{getElementById:k=>elements[k],createElement:()=>new Element()},window,EventSource,
  Date:class extends Date{static now(){return now}},
  setTimeout(fn,ms){const id=++next;timers.set(id,{fn,at:now+ms});return id},clearTimeout:id=>timers.delete(id),
  fetch:async(url,options)=>{requests.push({url,options});const r=responses.shift();if(!r)throw Error('unexpected GET');if(r.wait)return r.wait;return{ok:r.status===200,status:r.status,json:async()=>r.data}}
 });
 async function flush(){for(let i=0;i<12;i++)await Promise.resolve()}
 async function advance(ms){const end=now+ms;while(true){let ready=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!ready)break;now=ready[1].at;timers.delete(ready[0]);ready[1].fn();await flush()}now=end;await flush()}
 async function open(){window.EmberLobbyObserver.open({lobby_scope_id:'scope'});elements.lobbyToggle.onclick();await flush()}
 return{elements,filters,requests,streams,responses,snapshot,window,open,advance,flush};
}
const item=(id='one')=>({item_id:id,agent_id:'a',kind:'finding',status:'active',claim:'Observed result',version:1,expires_at:2000,created_at:1000,updated_at:1000,uncertainty:'observed',evidence_count:0,contribution_count:0});
test('one SSE, one initial snapshot and no GET during virtual healthy idle',async()=>{
 const f=fixture();await f.open();assert.equal(f.elements.lobbyState.textContent,'RECONNECTING');
 f.streams[0].emit('ready');await f.advance(180000);
 assert.equal(f.elements.lobbyState.textContent,'LIVE');assert.equal(f.requests.length,1);assert.equal(f.streams.length,1);
});
test('events refresh in place; duplicates and filters cause no extra GET',async()=>{
 const f=fixture();await f.open();f.streams[0].emit('ready');f.responses.push({status:200,data:f.snapshot(2,[item()])});f.streams[0].emit('working_memory_changed',2);await f.advance(50);
 const card=f.elements.lobbyItems.children[0];assert.ok(card);
 f.streams[0].emit('working_memory_changed',2);await f.advance(100);assert.equal(f.requests.length,2);
 f.filters[1].value='failure';f.filters[1].listeners.input();assert.equal(f.elements.lobbyItems.children.length,0);
 f.filters[1].value='';f.filters[1].listeners.input();assert.equal(f.elements.lobbyItems.children.length,1);assert.equal(f.requests.length,2);
});
test('disconnect, failed catch-up and recovery preserve cursor and one active stream',async()=>{
 const f=fixture();await f.open();f.streams[0].emit('ready');f.responses.push({status:503});f.streams[0].onerror();await f.advance(1500);
 assert.equal(f.elements.lobbyState.textContent,'RECONNECTING');f.responses.push({status:200,data:f.snapshot(4,[item()])});await f.advance(3000);
 assert.match(f.streams.at(-1).url,/after=4$/);assert.equal(f.streams.filter(s=>!s.closed).length,1);f.streams.at(-1).emit('ready');assert.equal(f.elements.lobbyState.textContent,'LIVE');
});
test('expired authorization terminates without reconnect loop',async()=>{
 const f=fixture();await f.open();f.responses.push({status:403});f.streams[0].onerror();await f.advance(180000);
 assert.equal(f.elements.lobbyState.textContent,'ACCESS ENDED');assert.equal(f.requests.length,2);assert.equal(f.streams.filter(s=>!s.closed).length,0);
});
test('clock expiry hides content without a request or mutation',async()=>{
 const f=fixture();f.responses[0].data=f.snapshot(1,[{...item(),expires_at:1001}]);await f.open();await f.advance(1100);
 assert.equal(f.requests.length,1);assert.match(f.elements.lobbyItems.children[0].children[0].textContent,/expired/);assert.doesNotMatch(f.elements.lobbyItems.children[0].children.map(c=>c.textContent).join(' '),/Observed result/);
});
test('reset reloads snapshot; old callbacks cannot overwrite a new grant',async()=>{
 const f=fixture();await f.open();f.responses.push({status:200,data:f.snapshot(9)});f.streams[0].emit('reset');await f.advance(1500);assert.match(f.streams[1].url,/after=9$/);
 const old=f.streams[1];f.window.EmberLobbyObserver.open({});old.emit('ready');old.emit('working_memory_changed',20);await f.advance(3000);assert.equal(f.elements.lobbyState.textContent,'NOT CONNECTED');assert.equal(f.requests.length,2);
});
test('leaving panel closes Lobby stream; returning performs bounded catch-up',async()=>{
 const f=fixture();await f.open();f.elements.lobbyToggle.onclick();assert.equal(f.streams[0].closed,true);
 f.responses.push({status:200,data:f.snapshot(5)});f.elements.lobbyToggle.onclick();await f.flush();assert.equal(f.requests.length,2);assert.equal(f.streams.filter(s=>!s.closed).length,1);
});
