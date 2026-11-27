"""Offline browser validation. Intercepts local page assets; starts no server."""
from pathlib import Path
import json,sys
from playwright.sync_api import sync_playwright
from embers.api import observer_build

OUT=Path(sys.argv[1] if len(sys.argv)>1 else 'docs/research-dashboard-evidence');OUT.mkdir(parents=True,exist_ok=True)

def node(i,ns='lab-A'):
    return dict(id=f'memory-id-{i}',namespace=ns,subject=['Retrieval strategies','Memory architecture','Context identity','Evidence correction','Latent discovery','Capacity limits','Route feedback','Verification'][i%8],primary_context='research',preview='Recorded fixture content for browser validation. No live server is involved.',usefulness=[dict(context='research',value=.6,N_eff=1)],provenance={'originating_agent':'fixture-agent','creation_event':'write-'+str(i),'source_ids':[]},truth_status='hypothesis',heat=.21 if i==0 else None)
def fixture(count=8):
    nodes=[node(i) for i in range(count)]
    edges=[dict(namespace='lab-A',from_=nodes[0]['id'],to=n['id'],type=['explains','requires','warns_about','alternative'][i%4],kind='pair_evidence',context='research',W=.6+i*.02,N_eff=i+1) for i,n in enumerate(nodes[1:])]
    for e in edges:e['from']=e.pop('from_')
    events=[dict(id='event-1',revision=1,namespace='lab-A',actor='fixture-agent',observation=dict(operation='ember_research_recall',context='research',direct_ids=['memory-id-0'],returned_ids=['memory-id-0','memory-id-1'],pair_expansion=dict(source='memory-id-0',target='memory-id-1',relation='explains',context='research',W=.6)))] if count else []
    return dict(namespace='lab-A',nodes=nodes,edges=edges,events=events)

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':960},reduced_motion='reduce');errors=[];requests=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def route(r):
        requests.append((r.request.method,r.request.url))
        if '/transport.js' in r.request.url:r.fulfill(body=observer_build.TRANSPORT,content_type='application/javascript')
        elif '/status' in r.request.url:r.fulfill(body=Path('embers/api/server_status.html').read_text(),content_type='text/html')
        elif '/health' in r.request.url:r.fulfill(json={'status':'ok','version':'fixture'})
        elif '/v1/observer/build' in r.request.url:r.fulfill(json=observer_build.metadata())
        else:r.fulfill(body=observer_build.render(namespace='/visualizer' in r.request.url),content_type='text/html')
    page.route('**/*',route)
    results=[]
    for surface in ['visualizer','observer']:
        page.goto('http://ember.test/'+surface);page.evaluate("applyTheme('dark')")
        page.evaluate("document.querySelectorAll('#accessDialog,#access').forEach(e=>e.hidden=true)")
        page.evaluate('''surface=>{window.feedFixture=d=>{if(surface==='visualizer'){window.EmberResearchUI.namespace(d);return}const last=d.events.at(-1)||{};window.EmberResearchUI.observer({...last,...last.observation,namespace:d.namespace,event_id:last.id||'fixture-state',observed_agent_id:'fixture-agent',memories:d.nodes,relationships:d.edges.filter(e=>e.kind!=='pair_evidence'),changes:d.edges.filter(e=>e.kind==='pair_evidence').map(e=>({after:{metric:'W',target:{kind:'pair',memory_ids:[e.from,e.to],relation:e.type},context:e.context,value:e.W,N_eff:e.N_eff}}))})}}''',surface)
        for count in [0,1,8,40]:
            sample=fixture(count)
            if count==40:
                sample['edges']=[dict(namespace='lab-A',**{'from':a['id']},to=b['id'],type='explains',kind='pair_evidence',context='research',W=.6) for a in sample['nodes'][:24] for b in sample['nodes'][:24] if a!=b]
            page.evaluate('window.EmberResearchUI.clear()');page.evaluate('d=>window.feedFixture(d)',sample)
            assert page.locator('#researchDashboard').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            if count in [0,1]:page.screenshot(path=str(OUT/f'{surface}-{count}-memories.png'))
        disconnected=fixture();disconnected['edges']=[];page.evaluate('window.EmberResearchUI.clear()');page.evaluate('d=>window.feedFixture(d)',disconnected);assert 'No relationships recorded' in page.locator('#rdMain').inner_text()
        d=fixture();page.evaluate('d=>window.feedFixture(d)',d)
        page.evaluate("window.savedGraphNode=document.querySelector('[data-node=\"memory-id-0\"]')");page.evaluate('d=>window.feedFixture(d)',d);assert page.evaluate("window.savedGraphNode===document.querySelector('[data-node=\"memory-id-0\"]')")
        page.locator('[data-node="memory-id-0"]').click();assert 'Helpful in this context' in page.locator('#rdInspector').inner_text()
        page.screenshot(path=str(OUT/f'{surface}-overview-dark.png'))
        page.locator('[data-view="matrix"]').click();assert page.locator('#rdContext').input_value()=='"research"';page.locator('#rdRelation').select_option('requires');assert page.locator('#rdRelation').input_value()=='requires';page.locator('#rdRelation').select_option('explains')
        page.locator('[data-pair]').nth(1).click();assert 'Pair weight (W)' in page.locator('#rdInspector').inner_text()
        page.screenshot(path=str(OUT/f'{surface}-matrix.png'))
        page.locator('[data-view="flow"]').click();page.locator('[data-stage="4"]').click();assert 'memory-id-1' in page.locator('#rdInspector').inner_text()
        page.locator('[data-stage="6"]').click();assert 'Not observed' in page.locator('#rdInspector').inner_text()
        page.screenshot(path=str(OUT/f'{surface}-flow.png'))
        page.locator('[data-view="simulation"]').click();before=page.evaluate('JSON.stringify(window.EmberResearchUI.getState())');page.locator('[data-action="motion"]').click();page.wait_for_timeout(200);assert before==page.evaluate('JSON.stringify(window.EmberResearchUI.getState())')
        assert 'fixture' not in page.locator('.rd-sim').inner_text().lower()
        page.locator('[data-view="overview"]').click();page.evaluate("applyTheme('light')");page.screenshot(path=str(OUT/f'{surface}-overview-light.png'))
        page.set_viewport_size({'width':1024,'height':768});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth');page.screenshot(path=str(OUT/f'{surface}-compact.png'));page.set_viewport_size({'width':1440,'height':960})
        results.append({'surface':surface,'empty_single_dense':True,'inspector_matrix_flow':True,'motion_readonly':True,'screen_fit':True})
    page.evaluate('window.EmberResearchUI.clear()')
    for ns in ['lab-A','lab-B']:
        page.evaluate('e=>window.EmberResearchUI.observer(e)',dict(namespace=ns,event_id='same-id',revision=1,observed_agent_id='same-agent',event_type='memory_written',memory_ids=['memory-id-0'],memories=[node(0,ns)],changes=[],relationships=[]))
    assert page.locator('#rdNamespace').input_value()=='lab-B'
    assert page.locator('#rdNamespace option').count()==2
    page.screenshot(path=str(OUT/'observer-multiple-namespaces.png'))
    page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth');page.set_viewport_size({'width':1440,'height':960})
    page.goto('http://ember.test/status');page.wait_for_selector('#health:text-is("Responding")');page.screenshot(path=str(OUT/'server-status.png'))
    assert not errors,errors
    assert all(method=='GET' for method,url in requests),requests
    (OUT/'browser-results.json').write_text(json.dumps(dict(results=results,multi_namespace=True,errors=errors,requests=requests,server_started=False),indent=2))
    browser.close()
