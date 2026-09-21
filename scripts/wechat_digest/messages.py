"""Message normalization; preserve unsupported media as explicit type markers."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zstandard

TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')
TYPES = {1: '文本', 3: '图片', 34: '语音', 42: '名片', 43: '视频', 47: '表情', 48: '位置',
         49: '卡片', 50: '通话', 10000: '系统', 10002: '系统'}


def time_window(hours=24, start=None, end=None, now=None):
    def parse(value):
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)
    if start and not end:
        raise ValueError('指定起点时必须同时指定终点')
    stop = parse(end) if end else (now or datetime.now(TZ)).astimezone(TZ)
    begin = parse(start) if start else stop - timedelta(hours=hours)
    if begin >= stop:
        raise ValueError('起点必须早于终点')
    return {'start': begin.isoformat(), 'end': stop.isoformat(), 'timezone': 'Asia/Shanghai (UTC+08:00)',
            'start_epoch': begin.timestamp(), 'end_epoch': stop.timestamp(), 'interval': '[start, end)'}


def decode_content(value, compression=0):
    if value is None:
        return '', []
    if isinstance(value, str):
        return value, []
    raw = bytes(value)
    try:
        if compression == 4 or raw.startswith(b'\x28\xb5\x2f\xfd'):
            raw = zstandard.ZstdDecompressor().decompress(raw, max_output_size=64*1024*1024)
        return raw.decode('utf-8'), []
    except (zstandard.ZstdError, UnicodeError):
        return '[正文解码失败]', ['未解析的正文 base64:' + base64.b64encode(raw).decode('ascii')]


def parse_body(content, local_type):
    kind = TYPES.get(local_type & 0xffffffff, '未知消息')
    result = {'type': kind, 'text': content, 'links': [], 'card': None, 'quote': None, 'warnings': []}
    if kind in ('图片', '视频', '语音', '表情', '通话', '位置', '名片', '未知消息'):
        result['text'] = f'[{kind}，未解析内容]'
    if content.lstrip().startswith('<'):
        try:
            if '<!DOCTYPE' in content.upper() or '<!ENTITY' in content.upper():
                raise ValueError('不解析 DTD/实体')
            root = ET.fromstring(content)
            app = root if root.tag == 'appmsg' else root.find('.//appmsg')
            if app is not None:
                card = {k: app.findtext(k) or '' for k in ('title', 'des', 'url', 'type', 'sourcedisplayname')}
                card['file_extension'] = app.findtext('appattach/fileext') or ''
                card['file_size'] = app.findtext('appattach/totallen') or ''
                result['card'] = card
                result['text'] = '\n'.join(x for x in (card['title'], card['des'], card['url']) if x) or '[卡片，内容未解析]'
                result['type'] = {'5': '链接/文章', '6': '文件', '57': '引用', '19': '合并转发'}.get(card['type'], '卡片')
                quote = app.find('refermsg')
                if quote is not None:
                    result['quote'] = {k: quote.findtext(k) or '' for k in ('svrid', 'fromusr', 'displayname', 'content', 'type', 'createtime')}
                if card['type'] == '19':
                    result['warnings'].append('仅解析转发卡片标题，未将嵌套转发当作本群新消息')
            # Only use text that explicitly exists in voice transcription fields.
            if kind == '语音':
                transcript = root.find('.//voicetrans')
                if transcript is not None:
                    text = transcript.get('transtext') or transcript.findtext('transtext') or ''
                    if text:
                        result['text'] = '[微信本地已有语音转写] ' + text
                        result['transcription_source'] = '消息 XML voicetrans/transtext'
        except (ET.ParseError, ValueError):
            result['warnings'].append('XML 未解析；保留原文供复核')
    result['links'] = list(dict.fromkeys(re.findall(r'https?://[^\s<>"\x27]+', result['text'])))
    return result


def normalize(row, shard, group_id, unit, sender_names):
    content, warnings = decode_content(row.get('message_content'), row.get('WCDB_CT_message_content', 0))
    sender = sender_names.get(row.get('real_sender_id'), '')
    prefix = re.match(r'^([A-Za-z0-9_@.-]+):\n', content)
    if prefix and (prefix[1] == sender or ((not sender or sender == group_id) and
                   (prefix[1].startswith('wxid_') or prefix[1] in sender_names.values()))):
        sender = prefix[1]
        content = content[prefix.end():]
    if sender == group_id:
        sender = ''
    parsed = parse_body(content, int(row['local_type']))
    stamp = int(row['create_time']) / unit
    server = str(row.get('server_id') or '0')
    local = str(row['local_id'])
    unique = f'{group_id}:server:{server}' if server not in ('0', '-1') else f'{group_id}:{shard}:{local}'
    return {'id': 'm_' + hashlib.sha256(unique.encode()).hexdigest()[:24], 'server_id': server,
            'local_id': local, 'group_id': group_id, 'sender_id': sender or f'unknown:{shard}:{row.get("real_sender_id")}',
            'sender_name': sender or '未识别发言人', 'sender_mapping': 'Name2Id/正文前缀' if sender else 'unresolved',
            'timestamp': datetime.fromtimestamp(stamp, TZ).isoformat(), 'timestamp_epoch': stamp,
            'raw_timestamp': row['create_time'], 'timestamp_unit': {1:'seconds',1000:'milliseconds',1000000:'microseconds'}[unit],
            'local_type': row['local_type'], 'raw_content': content, **parsed,
            'warnings': warnings + parsed['warnings'],
            'sources': [{'database': shard, 'table': 'Msg_' + hashlib.md5(group_id.encode()).hexdigest(), 'local_id': local}]}


def deduplicate(messages):
    seen, result, duplicates = {}, [], 0
    for message in sorted(messages, key=lambda m: (m['timestamp_epoch'], int(m['local_id']), m['sources'][0]['database'])):
        previous = seen.get(message['id'])
        if previous:
            if any(previous[k] != message[k] for k in ('raw_content', 'sender_id', 'local_type', 'timestamp_epoch')):
                raise ValueError('相同消息标识对应不同内容，不能自动去重：' + message['id'])
            previous['sources'].extend(message['sources'])
            duplicates += 1
        else:
            seen[message['id']] = message
            result.append(message)
    return result, duplicates


def stats(messages):
    senders = {m['sender_id'] for m in messages if m['type'] != '系统' and m['sender_mapping'] != 'unresolved'}
    types = {}
    for m in messages:
        types[m['type']] = types.get(m['type'], 0) + 1
    return {'message_count': len(messages), 'speaker_count': len(senders), 'types': types,
            'unresolved_sender_messages': sum(m['sender_mapping'] == 'unresolved' for m in messages)}


def write_messages(directory, metadata, messages):
    directory.mkdir(parents=True, exist_ok=False)
    data = {'schema_version': 1, 'metadata': {**metadata, **stats(messages)}, 'messages': messages}
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    (directory / 'messages.json').write_text(raw, encoding='utf-8')
    lines = [f"{metadata['group_name']} | {metadata['window']['start']} ≤ 时间 < {metadata['window']['end']}",
             '范围：本机已同步可用消息，不代表完整群历史。', '']
    for m in messages:
        lines.append(f"[{m['id']}] {m['timestamp']} {m['sender_name']} ({m['sender_id']}) [{m['type']}]\n{m['text']}")
        if m['quote']:
            lines.append('引用：' + json.dumps(m['quote'], ensure_ascii=False))
        lines.append('')
    (directory / 'messages.txt').write_text('\n'.join(lines), encoding='utf-8')
    # Small explicit batches make it possible for Codex to read ALL records without terminal truncation.
    batches = directory / 'batches'
    batches.mkdir()
    current, size, manifest = [], 0, []
    def save():
        if not current: return
        name = f'batch-{len(manifest)+1:04d}.json'
        text = json.dumps(current, ensure_ascii=False, indent=2)
        (batches / name).write_text(text, encoding='utf-8')
        manifest.append({'file': name, 'message_ids': [m['id'] for m in current], 'sha256': hashlib.sha256((batches / name).read_bytes()).hexdigest()})
    for message in messages:
        compact = {k: message[k] for k in ('id', 'timestamp', 'sender_name', 'type', 'text', 'quote', 'warnings')}
        length = len(json.dumps(compact, ensure_ascii=False))
        if current and size + length > 12000:
            save(); current, size = [], 0
        current.append(compact); size += length
    save()
    (batches / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return data
