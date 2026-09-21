import copy
import hashlib
import json
from pathlib import Path
from PIL import Image
import pytest
from wechat_digest.demo import demo
from wechat_digest.report import load, validate, render, screenshot


@pytest.fixture(scope='module')
def artifact(tmp_path_factory):
    return demo(tmp_path_factory.mktemp('reports'))


def test_report_evidence_rejections(artifact):
    report=load(artifact/'report.json')
    assert validate(artifact,report)['metadata']['message_count']==12
    bad=copy.deepcopy(report)
    bad['overview']['evidence'][0]['message_id']='fabricated'
    with pytest.raises(ValueError,match='不存在'):validate(artifact,bad)
    bad=copy.deepcopy(report)
    bad['overview']['evidence'][0]['quote']='原文根本没有这句话'
    with pytest.raises(ValueError,match='连续原文'):validate(artifact,bad)
    bad=copy.deepcopy(report);bad['reviewed_batches']=[]
    with pytest.raises(ValueError,match='全部批次'):validate(artifact,bad)


def test_offline_mobile_desktop_and_escaping(artifact):
    from playwright.sync_api import sync_playwright
    html=(artifact/'index.html').read_text(encoding='utf-8')
    assert '<离线安全测试>' not in html and '&lt;离线安全测试&gt;' in html
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        try:
            for width in (390,1000):
                page=browser.new_page(viewport={'width':width,'height':900})
                requests=[]; errors=[]
                page.on('request',lambda r:requests.append(r.url))
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto((artifact/'index.html').as_uri())
                assert page.title().startswith('虚构项目协作群')
                assert page.evaluate('document.documentElement.scrollWidth')==width
                assert page.locator('details[open]').count()==0
                assert page.locator('script').count()==0
                page.locator('details summary').first.click()
                assert page.locator('details[open]').count()==1
                assert not errors
                assert not any(u.startswith(('http:','https:')) for u in requests)
                # Explicit rendered font evidence, not just CSS configuration.
                session=page.context.new_cdp_session(page)
                dom=session.send('DOM.getDocument')
                node=session.send('DOM.querySelector',{'nodeId':dom['root']['nodeId'],'selector':'h1'})
                session.send('DOM.enable');session.send('CSS.enable')
                fonts=session.send('CSS.getPlatformFontsForNode',{'nodeId':node['nodeId']})['fonts']
                assert any('YaHei' in f['familyName'] for f in fonts)
                page.close()
        finally:browser.close()


def test_png_height_and_split_coverage(artifact,tmp_path):
    import shutil
    manifest=load(artifact/'png-manifest.json')
    with Image.open(artifact/'report.png') as image:
        assert image.width==1500
        assert abs(image.height-manifest['height_css_px']*1.5)<=1
        assert image.height>1350  # regression: screenshot was clamped to viewport
    target=tmp_path/'split';shutil.copytree(artifact,target)
    manifest=screenshot(target,max_height=700)
    assert len(manifest['files'])>1 and manifest['split_reason']
    assert sum(f['height_css_px'] for f in manifest['files'])==manifest['height_css_px']
    for f in manifest['files']:
        with Image.open(target/f['file']) as image:
            assert abs(image.height-f['height_css_px']*1.5)<=1
    assert (target/'PNG分图说明.txt').exists()
