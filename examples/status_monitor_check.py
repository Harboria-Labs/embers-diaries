"""Offline browser checks; no running Ember server or model calls."""
from pathlib import Path
import json,os
from playwright.sync_api import sync_playwright,expect
from embers.api import observer_build
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('EMBER_TEST_BROWSER'),headless=True,args=['--no-sandbox'])
    page=browser.new_page();requests=[];errors=[];mode={'value':'ok'}
    page.on('pageerror',lambda e:errors.append(str(e)))
    def route(r):
        path=r.request.url.split('http://ember.test')[-1];requests.append((r.request.method,path))
        if path=='/test':r.fulfill(body=Path('embers/api/server_status.html').read_text().replace('__STATUS_MONITOR__',observer_build.STATUS_MONITOR),content_type='text/html')
        elif path=='/header':r.fulfill(body='<header><span id="status">LIVE</span></header><script>'+observer_build.STATUS_MONITOR+'</script>',content_type='text/html')
        elif path=='/health':
            if mode['value']=='fail':r.abort()
            elif mode['value']=='blocked':r.fulfill(status=403,body='blocked')
            else:r.fulfill(json={'status':'ok' if mode['value']=='ok' else 'degraded'})
        else:r.fulfill(body='read only endpoint')
    page.route('**/*',route);page.clock.install();page.goto('http://ember.test/test')
    expect(page.locator('header .ember-health')).to_have_attribute('data-health','green')
    assert page.locator('.health-row').count()==7
    count=sum(path=='/health' for _,path in requests);mode['value']='fail';page.clock.run_for(15100)
    expect(page.locator('header .ember-health')).to_have_attribute('data-health','red')
    assert sum(path=='/health' for _,path in requests)==count+1
    mode['value']='partial';page.clock.run_for(15100);expect(page.locator('header .ember-health')).to_have_attribute('data-health','yellow')
    mode['value']='blocked';page.clock.run_for(15100);expect(page.locator('header .ember-health')).to_have_attribute('data-health','yellow')
    mode['value']='ok';page.clock.run_for(15100);expect(page.locator('header .ember-health')).to_have_attribute('data-health','green')
    page.goto('http://ember.test/header');expect(page.locator('header .ember-health').first).to_have_attribute('data-health','green')
    expect(page.locator('header .ember-health').last).to_have_attribute('data-health','green')
    page.evaluate("document.getElementById('status').textContent='RECONNECTING'");expect(page.locator('header .ember-health').last).to_have_attribute('data-health','yellow')
    page.evaluate("document.getElementById('status').textContent='ACCESS EXPIRED'");expect(page.locator('header .ember-health').last).to_have_attribute('data-health','red')
    page.evaluate("performance.now=(()=>{let t=0;return ()=>t+=2500})()");page.clock.run_for(15100)
    expect(page.locator('header .ember-health').first).to_have_attribute('data-health','yellow')
    expect(page.locator('header .ember-health').first).to_contain_text('Slow')
    assert all(method=='GET' for method,path in requests)
    assert not any('snapshot' in path or '/memory' in path or '/stream' in path for _,path in requests)
    assert not errors,errors
    print(json.dumps({'automatic_refresh':True,'failure_recovery':True,'partial_blocked':True,'independent_SSE_status':True,'public_GET_only':True,'browser_errors':errors}))
    browser.close()
