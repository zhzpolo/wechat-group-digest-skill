"""Strict Weixin 4.x schema adapter. Unknown schema is an error, never a guess."""
from contextlib import ExitStack
from datetime import datetime
import hashlib
from pathlib import Path
import re
import uuid
from .messages import TZ, deduplicate, normalize, write_messages
from .storage import database, snapshots
from .windows import accounts, processes, extract_keys
from .capture import capture_keys


def columns(db, table):
    if not re.fullmatch(r'[A-Za-z0-9_]+', table):
        raise ValueError('不安全的表名')
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def require(db, table, expected):
    missing = set(expected) - columns(db, table)
    if missing:
        raise ValueError(f'未验证的数据库结构：{table} 缺少 {sorted(missing)}')


def match_group(db, name, group_id=None):
    require(db, 'contact', ('username', 'nick_name'))
    matches = [{'id': uid, 'name': nick} for uid, nick in db.execute(
        "SELECT username,nick_name FROM contact WHERE nick_name = ? AND username LIKE '%@chatroom'", (name,))]
    if group_id:
        matches = [g for g in matches if g['id'] == group_id]
    if not matches:
        raise ValueError('没有完整名称完全匹配的群。请核对当前微信里的完整群名；不会使用模糊匹配替代。')
    if len(matches) > 1:
        details = []
        fields = columns(db, 'chat_room')
        for group in matches:
            item = dict(group)
            if {'username', 'owner'}.issubset(fields):
                row = db.execute('SELECT owner FROM chat_room WHERE username=?', (group['id'],)).fetchone()
                item['owner'] = row[0] if row else '未知'
            details.append(item)
        raise ValueError('有多个同名群，请用 --group-id 选择稳定 ID：' + str(details))
    return matches[0]


def timestamp_unit(minimum, maximum):
    if minimum is None:
        return 1
    def unit(value):
        # Weixin was released after 2000; reject unknown/mixed timestamp ranges.
        for factor in (1, 1000, 1000000):
            if 946684800 <= value / factor < 4102444800:
                return factor
        raise ValueError('无法验证消息时间戳单位')
    if unit(minimum) != unit(maximum):
        raise ValueError('同一消息表存在混合时间戳单位')
    return unit(minimum)


def read_group(contact, shard_connections, group, window):
    messages, audit = [], []
    table = 'Msg_' + hashlib.md5(group['id'].encode('utf-8')).hexdigest()
    for shard, db in shard_connections:
        if not db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone():
            audit.append({'database': shard, 'target_table_present': False, 'selected_rows': 0})
            continue
        required = ('local_id', 'local_type', 'create_time', 'real_sender_id', 'message_content')
        require(db, table, required)
        require(db, 'Name2Id', ('user_name',))
        available = columns(db, table)
        selected = list(required) + [c for c in ('server_id', 'WCDB_CT_message_content') if c in available]
        low, high = db.execute(f'SELECT MIN(create_time), MAX(create_time) FROM "{table}"').fetchone()
        factor = timestamp_unit(low, high)
        bounds = (window['start_epoch']*factor, window['end_epoch']*factor)
        where = 'create_time >= ? AND create_time < ?'
        expected = db.execute(f'SELECT count(*) FROM "{table}" WHERE {where}', bounds).fetchone()[0]
        # Resolve only senders present in the selected window.
        sender_ids = [r[0] for r in db.execute(f'SELECT DISTINCT real_sender_id FROM "{table}" WHERE {where}', bounds)]
        names = {}
        for sid in sender_ids:
            row = db.execute('SELECT user_name FROM Name2Id WHERE rowid=?', (sid,)).fetchone()
            if row: names[sid] = row[0]
        count = 0
        for row in db.execute(f'SELECT {",".join(selected)} FROM "{table}" WHERE {where} ORDER BY create_time, local_id', bounds):
            messages.append(normalize(dict(zip(selected, row)), shard, group['id'], factor, names))
            count += 1
        if count != expected:
            raise RuntimeError('计数与遍历结果不一致；不生成不完整导出')
        audit.append({'database': shard, 'target_table_present': True, 'selected_rows': count,
                      'timestamp_unit_factor': factor, 'local_earliest_epoch': low/factor if low else None,
                      'local_latest_epoch': high/factor if high else None})
    unique, duplicate_count = deduplicate(messages)
    require(contact, 'contact', ('username', 'nick_name'))
    display = {}
    for sid in {m['sender_id'] for m in unique if m['sender_mapping'] != 'unresolved'}:
        row = contact.execute('SELECT nick_name FROM contact WHERE username=?', (sid,)).fetchone()
        display[sid] = row[0] if row and row[0] else sid
    for message in unique:
        message['sender_name'] = display.get(message['sender_id'], message['sender_name'])
        if not window['start_epoch'] <= message['timestamp_epoch'] < window['end_epoch']:
            raise AssertionError('时间边界错误')
    return unique, audit, duplicate_count


def _wipe(keys):
    for key in keys.values():
        key[:] = bytes(len(key))


def acquire_access(paths, pages, procs, capture=None, capture_seconds=120,
                   restart_fallback=False, online_attempts=30):
    """Prefer a verified online snapshot; restart only after bounded failure.

    A usable online state requires both an encrypted snapshot that passes the
    DB/WAL/SHM stability checks and a complete set of HMAC-verified keys. Keys
    never leave mutable process memory. The fallback never kills WeChat: it
    waits for the user to exit it from the tray, then instruments startup.
    """
    if online_attempts < 1:
        raise ValueError('--online-attempts 必须大于 0')
    if len(procs) > 1:
        raise ValueError('微信主进程不唯一，请用 --pid 明确选择：' + str([p['pid'] for p in procs]))
    online_reason = None
    if procs:
        print(f'优先在线读取：微信保持登录，最多进行 {online_attempts} 次稳定快照检查。', flush=True)
        try:
            copies = snapshots(paths, attempts=online_attempts)
        except RuntimeError:
            online_reason = f'连续 {online_attempts} 次未取得通过 DB/WAL/SHM 校验的稳定快照'
        else:
            try:
                keys = extract_keys(procs[0]['pid'], pages)
            except RuntimeError:
                online_reason = '稳定快照已取得，但在线内存中未找到全部通过 HMAC 的数据库密钥'
            else:
                print('在线稳定快照与全部数据库密钥校验通过；无需退出微信。', flush=True)
                return keys, copies, {'mode': 'online', 'online_attempts': online_attempts,
                                      'restart_required': False, 'fallback_reason': None}
    else:
        online_reason = '微信当前未运行，无法进行在线读取'

    if not restart_fallback:
        raise RuntimeError(online_reason + '；本次未启用启动捕获降级，可添加 --restart-fallback 和 --capture')
    if not capture:
        raise ValueError('在线读取未满足完整条件，启动捕获降级需要 --capture ANCHORS_JSON')

    print('在线读取未满足完整条件：' + online_reason, flush=True)
    print('现在才进入退出降级流程；不会强制关闭微信，也不会保存密钥。', flush=True)
    keys = capture_keys(procs[0]['pid'] if procs else 0, pages, capture,
                        capture_seconds, wait_for_close=True)
    try:
        copies = snapshots(paths, attempts=online_attempts)
    except BaseException:
        _wipe(keys)
        raise
    return keys, copies, {'mode': 'startup_capture_fallback', 'online_attempts': online_attempts,
                          'restart_required': True, 'fallback_reason': online_reason}


def export(group_name, window, output_root, account=None, pid=None, group_id=None, capture=None,
           capture_seconds=120, restart_fallback=False, online_attempts=30):
    roots = accounts()
    if account:
        chosen = Path(account).resolve()
        if chosen not in roots:
            raise ValueError('--account 必须是自动检测到的 db_storage 路径')
    elif len(roots) == 1:
        chosen = roots[0]
    else:
        raise ValueError('账号不唯一，请用 --account 明确选择：' + str([str(p) for p in roots]))
    procs = processes()
    if pid is not None:
        procs = [p for p in procs if p['pid'] == pid]
    if len(procs) != 1 and not (restart_fallback and not procs):
        raise ValueError('微信主进程不唯一，请用 --pid 明确选择：' + str([p['pid'] for p in procs]))
    # Exact salt + HMAC binds every retained key to this one account directory.
    shards = sorted(p for p in (chosen / 'message').glob('message_*.db') if re.fullmatch(r'message_\d+\.db', p.name))
    if not shards:
        raise ValueError('未找到标准微信 4.x 消息分片；不能验证兼容性')
    contact_path = chosen / 'contact/contact.db'
    paths = [contact_path] + shards
    pages = {}
    for path in paths:
        with path.open('rb') as f: pages[path.name] = f.read(4096)
    keys, copies, access = acquire_access(paths, pages, procs, capture, capture_seconds,
                                          restart_fallback, online_attempts)
    try:
        with ExitStack() as stack:
            opened = {p: stack.enter_context(database(copies[p], keys[p.name])) for p in paths}
            contact = opened[contact_path][0]
            group = match_group(contact, group_name, group_id)
            messages, audit, duplicates = read_group(contact, [(p.name, opened[p][0]) for p in shards], group, window)
            if sorted(p.name for p in (chosen / 'message').glob('message_*.db') if re.fullmatch(r'message_\d+\.db', p.name)) != sorted(p.name for p in shards):
                raise RuntimeError('读取过程中新增/删除分片，请重试')
            capture = [view.info for _, view in opened.values()]
        warnings = ['仅包含此账号、本机已同步的可用消息；无法仅从本地数据库证明云端/手机记录完整。',
                    '快照采用连续两次相同读取、文件状态及 WAL-index 校验；不是跨数据库的全局事务。',
                    '发言人显示通讯录昵称及稳定 ID；未解析群内自定义昵称。',
                    '未解析图片、视频、表情或音频内容。']
        earliest = [a['local_earliest_epoch'] for a in audit if a.get('local_earliest_epoch')]
        if earliest and min(earliest) > window['start_epoch']:
            warnings.append('本地最早消息晚于统计起点；可能同步不足，也可能此前无消息，不能据此断言历史完整。')
        if not messages:
            warnings.append('所选窗口无可用消息；不能据此推断该群没有活动。')
        metadata = {'group_name': group['name'], 'group_id': group['id'], 'account_id': chosen.parent.name,
                    'window': window, 'created_at': datetime.now(TZ).isoformat(), 'synthetic': False,
                    'reader': 'wechat_digest/Weixin4-strict', 'deduplicated_count': duplicates,
                    'raw_selected_count': sum(a['selected_rows'] for a in audit), 'shards': audit,
                    'snapshots': capture, 'access': access, 'warnings': warnings,
                    'plaintext_database_files_created': 0}
        directory = Path(output_root) / (datetime.now(TZ).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
        write_messages(directory, metadata, messages)
        return directory
    finally:
        _wipe(keys)
