"""Validate human/Codex-authored conclusions and render fully offline artifacts."""
from datetime import datetime
import hashlib
import html
import json
import math
from pathlib import Path
import re
from .messages import stats, TZ

SECTIONS = [('topics', '主要话题及讨论结论'), ('todos', '待办事项'), ('resolved', '已解决或已确认事项'),
            ('open_questions', '尚未解决的问题'), ('other', '其他信息')]
CATEGORIES = {'群内观点', '建议', '决定', '收到', '同意', '执行完成', '已确认', '未解决', '其他'}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate(directory, report):
    data = load(directory / 'messages.json')
    messages = {m['id']: m for m in data['messages']}
    if len(messages) != len(data['messages']):
        raise ValueError('messages.json 存在重复 ID')
    for k, value in stats(data['messages']).items():
        if data['metadata'][k] != value:
            raise ValueError('统计不一致：' + k)
    digest = hashlib.sha256((directory / 'messages.json').read_bytes()).hexdigest()
    if report.get('messages_sha256') != digest:
        raise ValueError('总结对应的 messages.json 摘要不一致')
    manifest = load(directory / 'batches/manifest.json')
    if report.get('reviewed_batches') != [b['sha256'] for b in manifest]:
        raise ValueError('必须读完全部批次，并在 report.json 登记 reviewed_batches')
    all_ids = []
    for batch in manifest:
        path = directory / 'batches' / batch['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != batch['sha256']:
            raise ValueError('批次文件已改变')
        all_ids.extend(batch['message_ids'])
    if all_ids != [m['id'] for m in data['messages']]:
        raise ValueError('批次没有完整覆盖消息')
    if report.get('synthetic') is not data['metadata']['synthetic']:
        raise ValueError('真实/虚构标记不一致')
    overview = report.get('overview')
    if not isinstance(overview, dict) or not overview.get('text'):
        raise ValueError('缺少概览')
    claims = [overview]
    for section, _ in SECTIONS:
        if not isinstance(report.get(section), list):
            raise ValueError('缺少结构化章节：' + section)
        claims.extend(report[section])
    for claim in claims:
        if not isinstance(claim.get('text'), str) or not claim['text'].strip():
            raise ValueError('结论不能为空')
        if claim.get('category') not in CATEGORIES:
            raise ValueError('必须明确区分建议、决定、收到、同意、执行完成等类别')
        evidence = claim.get('evidence', [])
        if messages and not evidence:
            raise ValueError('每条结论必须关联证据')
        for item in evidence:
            message = messages.get(item.get('message_id'))
            if message is None:
                raise ValueError('引用消息不存在：' + str(item.get('message_id')))
            quote = item.get('quote')
            if not isinstance(quote, str) or not quote.strip() or quote not in message['text']:
                raise ValueError('证据摘录必须是对应消息正文中的连续原文：' + message['id'])
    for task in report['todos']:
        for key in ('owner', 'deadline', 'status'):
            if not isinstance(task.get(key), str) or not task[key].strip():
                raise ValueError('待办缺少 ' + key + '；无法确定时写“未明确”')
    return data


STYLE = '''
:root{color-scheme:light;--ink:#142e3d;--muted:#5c6e78;--line:#d9e3e7;--accent:#176663}
*{box-sizing:border-box}body{margin:0;background:#eef3f4;color:var(--ink);font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;font-size:16px;line-height:1.8}
main{max-width:1000px;margin:28px auto;background:#fff;padding:48px 56px;border-radius:20px;box-shadow:0 8px 36px #16313d0d}
.eyebrow{color:var(--accent);font-size:13px;font-weight:700;letter-spacing:2px}h1{font-size:32px;line-height:1.4;margin:10px 0 18px;overflow-wrap:anywhere}h2{font-size:21px;margin:30px 0 14px;padding-bottom:10px;border-bottom:1px solid var(--line)}
p{margin:8px 0}a{color:var(--accent);overflow-wrap:anywhere}.meta{font-size:13px;color:var(--muted);overflow-wrap:anywhere}.stats{display:flex;gap:12px;margin:24px 0}.stat{flex:1;background:#f1f7f6;border-radius:12px;padding:14px 20px}.number{font-size:29px;font-weight:700;color:var(--accent)}.label{font-size:13px;color:var(--muted)}
.overview{border-left:4px solid var(--accent);padding:14px 20px;background:#f6f9fa;border-radius:0 10px 10px 0}.claim{padding:13px 0;break-inside:avoid}.claim+.claim{border-top:1px dashed var(--line)}.badge{display:inline-block;background:#edf3f6;padding:1px 8px;margin:0 8px 4px 0;border-radius:5px;font-size:12px;color:#456271}.text{white-space:pre-wrap;overflow-wrap:anywhere}.refs{font-size:12px;margin-left:8px;white-space:normal}.task-meta{display:flex;flex-wrap:wrap;gap:8px 22px;font-size:13px;color:var(--muted);margin-top:8px}
.notice{background:#fff8e9;border:1px solid #ede0bd;padding:16px 20px;border-radius:10px;font-size:13px;color:#685c39;margin-top:26px}.notice ul{padding-left:20px;margin:8px 0}.empty{color:var(--muted);font-size:14px}.source{padding:12px 0;border-bottom:1px solid var(--line)}summary{cursor:pointer;font-size:14px}blockquote{margin:12px 0;padding:12px 16px;border-left:3px solid #b9cecf;background:#f7f9fa;white-space:pre-wrap;overflow-wrap:anywhere}.foot{margin-top:30px;padding-top:16px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
.synthetic{background:#a44523;color:#fff;font-size:14px;padding:9px 15px;border-radius:8px;margin-bottom:18px}.print .sources{display:none}.print main{margin:0;border-radius:0;box-shadow:none;max-width:none;padding:40px 48px}.print body{background:#fff}.print .refs a{text-decoration:none}.print .notice{margin-bottom:0}
@media(max-width:600px){main{margin:0;padding:24px 20px;border-radius:0}h1{font-size:25px}.stats{gap:8px}.stat{padding:10px 12px}.number{font-size:25px}.task-meta{display:block}}
'''


def render(directory):
    directory = Path(directory)
    report = load(directory / 'report.json')
    data = validate(directory, report)
    meta, messages = data['metadata'], {m['id']: m for m in data['messages']}
    esc = lambda s: html.escape(str(s), quote=True)
    references = {}
    for claim in [report['overview']] + [c for key, _ in SECTIONS for c in report[key]]:
        for e in claim['evidence']:
            pair = (e['message_id'], e['quote'])
            if pair not in references: references[pair] = len(references) + 1
    def refs(claim):
        return '<span class="refs">' + ' '.join(f'<a href="#source-{references[(e["message_id"], e["quote"])]}">[{references[(e["message_id"], e["quote"])]}]</a>' for e in claim['evidence']) + '</span>'
    def claim_html(claim, task=False):
        body = f'<div class="claim"><span class="badge">{esc(claim["category"])}</span><span class="text">{esc(claim["text"])}</span>{refs(claim)}'
        if task:
            body += '<div class="task-meta">' + ''.join(f'<span>{label}：{esc(claim[k])}</span>' for k,label in [('owner','负责人'),('deadline','时间要求'),('status','状态')]) + '</div>'
        return body + '</div>'
    window = meta['window']
    sections = ''.join(f'<section><h2>{title}</h2>' + (''.join(claim_html(c, key == 'todos') for c in report[key]) or '<p class="empty">本次记录中未发现明确事项。</p>') + '</section>' for key, title in SECTIONS)
    sources = '<section class="sources"><h2>来源核对</h2><p class="meta">默认折叠，仅包含结论所需摘录。完整原始导出见同目录 messages.json / messages.txt。</p>'
    for (message_id, quote), index in references.items():
        m = messages[message_id]
        sources += f'<details class="source" id="source-{index}"><summary>[{index}] {esc(m["sender_name"])} · {esc(m["timestamp"])} · {esc(m["type"])}</summary><p class="meta">消息 ID：{esc(message_id)}<br>稳定发送人 ID：{esc(m["sender_id"])}</p><blockquote>{esc(quote)}</blockquote><p class="meta">{esc(json.dumps(m["sources"],ensure_ascii=False))}</p></details>'
    sources += '</section>'
    author_note = '总结为固定虚构测试样例。' if meta['synthetic'] else '总结由当前 Codex 会话阅读全部导出后撰写。'
    warnings = list(meta['warnings']) + ['群内观点未经外部事实核验；“收到”“同意”“已完成”按原文区分。', author_note + '程序校验引用存在及摘录匹配，不自动证明结论语义。']
    if meta.get('synthetic'): warnings.insert(0, '这是虚构测试数据，不能作为真实微信读取成功的证据。')
    doc = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'"><title>{esc(meta['group_name'])} · 聊天摘要</title><style>{STYLE}</style></head><body><main>
    {'<div class="synthetic">虚构数据演示 · 非真实群聊报告</div>' if meta['synthetic'] else ''}<div class="eyebrow">WECHAT · LOCAL DIGEST</div><h1>{esc(meta['group_name'])}</h1><div class="meta">{esc(window['start'])} ≤ 时间 &lt; {esc(window['end'])}<br>{esc(window['timezone'])} · 本地已同步记录<br>群 ID：{esc(meta['group_id'])}</div>
    <div class="stats"><div class="stat"><div class="number">{meta['message_count']}</div><div class="label">条消息</div></div><div class="stat"><div class="number">{meta['speaker_count']}</div><div class="label">位可识别发言人</div></div><div class="stat"><div class="number">{len(report['todos'])}</div><div class="label">项待办</div></div></div>
    <div class="overview"><div class="text">{esc(report['overview']['text'])}</div>{refs(report['overview'])}</div>{sections}<div class="notice"><strong>读取范围与说明</strong><ul>{''.join('<li>'+esc(w)+'</li>' for w in warnings)}</ul></div>{sources}<div class="foot">生成时间：{esc(datetime.now(TZ).isoformat())} · 离线报告 · 未部署到公网<br>来源编号对应 HTML 中可展开的必要摘录。导出与核对文件保存在同一目录。</div></main></body></html>'''
    (directory / 'index.html').write_text(doc, encoding='utf-8')
    def md(s):
        return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    lines = [f"# {md(meta['group_name'])}｜聊天总结", '', '**虚构数据测试，不是真实报告。**' if meta['synthetic'] else '',
             f"时间：{window['start']} ≤ 时间 < {window['end']}（{window['timezone']}）", '',
             f"群 ID：{meta['group_id']}；消息 {meta['message_count']} 条；可识别发言人 {meta['speaker_count']} 位。", '', '## 概览', '', md(report['overview']['text']), '']
    def add_claim(c):
        citation = ' '.join(f"[{e['message_id']}]" for e in c['evidence'])
        lines.append(f"- **{c['category']}**：{md(c['text'])} {citation}")
    lines.append('来源：' + ' '.join(e['message_id'] for e in report['overview']['evidence']))
    for key, title in SECTIONS:
        lines.extend(['', '## ' + title, ''])
        if not report[key]: lines.append('本次记录中未发现明确事项。')
        for c in report[key]:
            add_claim(c)
            if key == 'todos': lines.append(f"  负责人：{md(c['owner'])}；时间要求：{md(c['deadline'])}；状态：{md(c['status'])}。")
    lines.extend(['', '## 来源摘录', ''])
    for (mid, quote), index in references.items():
        lines.extend([f"[{index}] {mid} · {md(messages[mid]['sender_name'])} · {messages[mid]['timestamp']}", '', '> ' + md(quote).replace('\n','\n> '), ''])
    lines.extend(['## 范围与限制', ''] + ['- ' + md(w) for w in warnings])
    (directory / 'summary.md').write_text('\n'.join(lines), encoding='utf-8')
    return data


def screenshot(directory, max_height=14000):
    from playwright.sync_api import sync_playwright
    directory = Path(directory).resolve()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        try:
            page = browser.new_page(viewport={'width': 1000, 'height': 900}, device_scale_factor=1.5)
            page.route('http://**/*', lambda r: r.abort())
            page.route('https://**/*', lambda r: r.abort())
            page.goto((directory / 'index.html').as_uri(), wait_until='load')
            page.evaluate('document.documentElement.classList.add("print")')
            page.evaluate('document.fonts.ready')
            height = math.ceil(page.locator('main').bounding_box()['height'])
            width = page.evaluate('document.documentElement.scrollWidth')
            if width > 1000:
                raise ValueError('报告出现水平溢出，停止生成可能被截断的 PNG')
            # Split at element boundaries when possible. Never silently crop.
            edges = page.locator('.claim,section,.notice,.foot').evaluate_all('(els)=>els.map(e=>e.getBoundingClientRect().top+window.scrollY)')
            text_lines = page.evaluate('''() => {
              const walk = document.createTreeWalker(document.querySelector('main'), NodeFilter.SHOW_TEXT);
              const lines=[]; let node;
              while(node=walk.nextNode()) {
                if(!node.textContent.trim()) continue;
                const range=document.createRange();range.selectNodeContents(node);
                for(const r of range.getClientRects()) if(r.height>0) lines.push([r.top+scrollY,r.bottom+scrollY]);
              }
              return lines;
            }''')
            segments, y = [], 0
            while y < height:
                end = min(height, y + max_height)
                if end < height:
                    choices = [int(v) for v in edges if y + max_height*.55 < v <= end]
                    if not choices:
                        choices = [math.ceil(b) for a,b in text_lines if y + max_height*.55 < b <= end]
                    choices = [v for v in choices if not any(a < v < b for a,b in text_lines)]
                    if not choices:
                        raise ValueError('无法找到不会切断文字的分图位置，请调整报告排版')
                    end = max(choices)
                segments.append((y, end))
                y = end
            files = []
            for i, (top, bottom) in enumerate(segments, 1):
                name = 'report.png' if len(segments) == 1 or i == 1 else f'report-{i:03d}.png'
                page.screenshot(path=str(directory/name), full_page=True, clip={'x':0,'y':top,'width':1000,'height':bottom-top}, animations='disabled')
                from PIL import Image
                with Image.open(directory/name) as image:
                    if image.width != 1500 or abs(image.height - (bottom-top)*1.5) > 1:
                        raise ValueError('PNG 实际像素与完整内容高度不符，拒绝交付截断图片')
                files.append({'file':name,'part':i,'of':len(segments),'top_css_px':top,'height_css_px':bottom-top})
            manifest = {'renderer': 'Microsoft Edge / Playwright', 'browser_version': browser.version, 'height_css_px':height,
                        'width_css_px':1000,'device_scale_factor':1.5,'files':files,
                        'split_reason':'内容超过单图高度上限，依次阅读全部编号分图；report.png 是第 1 张。' if len(files)>1 else None}
            (directory/'png-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            if len(files)>1:
                (directory/'PNG分图说明.txt').write_text(manifest['split_reason']+'\n'+'\n'.join(f['file'] for f in files),encoding='utf-8')
            return manifest
        finally:
            browser.close()
