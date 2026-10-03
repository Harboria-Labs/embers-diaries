"""Offline educational settings UI checks. No listening server or live mutations."""
from pathlib import Path
from dataclasses import asdict
import json
from playwright.sync_api import sync_playwright, expect
from embers.core.domain import call
from embers.cognitive.usefulness import UsefulnessPolicy
from embers.integration.research_schema import schema

out=Path('docs/research-dashboard-evidence');out.mkdir(exist_ok=True)
config=call('config_default',None);policy=asdict(UsefulnessPolicy())
fixture=dict(call('config',config),policy=policy,fields=schema(config,policy),model_version='offline browser fixture',configuration_revision=3,journal_revision=42,can_edit=False,history_limit=30,history_truncated=False,configuration_history=[dict(configuration_revision=3,revision=40,actor='fixture-agent',created_at='2026-10-03T10:00:00Z',reason='Recorded capacity experiment',research_config=config,policy=policy)])
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':960});errors=[];requests=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def route(r):
        requests.append((r.request.method,r.request.url))
        if '/v1/research/settings/' in r.request.url:r.fulfill(json=fixture)
        elif '/v1/research/validate/' in r.request.url:r.fulfill(json=call('config',config))
        else:r.fulfill(body=Path('embers/api/research_settings.html').read_text(),content_type='text/html')
    page.route('**/*',route)
    page.goto('http://ember.test/research/settings?mode=observer')
    assert not page.locator('#auth').is_visible()
    assert not page.locator('#save').is_visible()
    assert 'Not observed' in page.locator('#accessMode').inner_text()
    assert all('/v1/' not in url for method,url in requests)
    assert page.locator('#pairing').inner_text().count('One hop')==1
    page.screenshot(path=str(out/'settings-observer-guide.png'))
    page.goto('http://ember.test/research/settings')
    page.locator('#namespace').fill('fixture-lab');page.locator('#agent').fill('fixture-reader')
    page.locator('#auth button').click()
    expect(page.locator('#revision')).to_have_text('3')
    assert page.locator('#save').is_disabled()
    assert page.locator('[data-path]').count()==len(fixture['fields'])
    assert page.locator('[data-path]:enabled').count()==0
    page.locator('#fields details').first.locator('summary').click()
    assert page.locator('#fields details').first.locator('.equation').is_visible()
    page.locator('#history summary').click()
    assert 'Recorded capacity experiment' in page.locator('#history').inner_text()
    page.screenshot(path=str(out/'settings-history.png'));page.locator('main').evaluate('(e)=>e.scrollTop=0');page.screenshot(path=str(out/'settings-dark.png'))
    page.locator('#theme').click();page.screenshot(path=str(out/'settings-light.png'))
    for width,height in [(1440,960),(1024,768),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        if width>650:assert page.evaluate('document.documentElement.scrollHeight<=innerHeight')
    assert all(method=='GET' for method,url in requests)
    fixture['can_edit']=True;page.set_viewport_size({'width':1440,'height':960})
    page.locator('#auth button').click();expect(page.locator('[data-path]:enabled')).to_have_count(len(fixture['fields']))
    page.locator('[data-path="config.max_results"]').fill('5');assert page.locator('#save').is_disabled()
    page.locator('#validate').click();expect(page.locator('#save')).to_be_enabled()
    page.locator('#agent').fill('different-agent');assert page.locator('#save').is_disabled()
    assert page.locator('#revision').inner_text()=='—'
    assert not errors,errors
    result=dict(offline=True,observer_zero_api_calls=True,reader_zero_posts=True,revision_history=True,details=True,themes=True,screen_fit=True,editor_validation=True,credential_change_clears_authority=True,browser_errors=errors)
    (out/'settings-browser-results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result));browser.close()
