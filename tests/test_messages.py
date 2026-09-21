from datetime import datetime
import hashlib
import apsw
import pytest
import zstandard
from wechat_digest.messages import time_window, normalize, deduplicate, parse_body, decode_content
from wechat_digest.reader import match_group, read_group, timestamp_unit


def test_group_exact_ambiguity():
    c=apsw.Connection(':memory:')
    c.execute('CREATE TABLE contact(username TEXT,nick_name TEXT)')
    c.executemany('INSERT INTO contact VALUES(?,?)',[('a@chatroom','测试群'),('b@chatroom','测试群'),('c@chatroom','测试群2'),('user','测试群')])
    with pytest.raises(ValueError,match='多个同名群'): match_group(c,'测试群')
    assert match_group(c,'测试群','b@chatroom')['id']=='b@chatroom'
    with pytest.raises(ValueError,match='完全匹配'): match_group(c,'测试')


def test_range_shards_dedup_and_full_read():
    w=time_window(start='2026-09-19T09:00:00+08:00',end='2026-09-20T09:00:00+08:00')
    gid='test@chatroom'; table='Msg_'+hashlib.md5(gid.encode()).hexdigest()
    contact=apsw.Connection(':memory:')
    contact.execute('CREATE TABLE contact(username TEXT,nick_name TEXT)')
    contact.execute('INSERT INTO contact VALUES(?,?)',('alice','小甲'))
    shards=[]
    for n, factor in enumerate((1,1000)):
        c=apsw.Connection(':memory:')
        c.execute('CREATE TABLE Name2Id(user_name TEXT)')
        c.execute("INSERT INTO Name2Id VALUES('alice')")
        c.execute(f'CREATE TABLE {table}(local_id INTEGER,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content BLOB,WCDB_CT_message_content INTEGER)')
        # 402 records per shard, overlapping stable server IDs, no LIMIT.
        rows=[(i,i,1,int(w['start_epoch']+i-1)*factor,1,zstandard.ZstdCompressor().compress(f'第{i}条虚构消息'.encode()),4) for i in range(1,401)]
        rows += [(401,401,1,int(w['start_epoch']-1)*factor,1,b'before',0),(402,402,1,int(w['end_epoch'])*factor,1,b'end',0)]
        c.executemany(f'INSERT INTO {table} VALUES(?,?,?,?,?,?,?)',rows)
        shards.append((f'message_{n}.db',c))
    messages,audit,duplicates=read_group(contact,shards,{'id':gid},w)
    assert len(messages)==400 and duplicates==400
    assert messages[0]['timestamp_epoch']==w['start_epoch']
    assert all(m['timestamp_epoch']<w['end_epoch'] for m in messages)
    assert all(m['sender_name']=='小甲' for m in messages)
    assert sum(a['selected_rows'] for a in audit)==800
    assert messages[-1]['text']=='第400条虚构消息'


def test_parsers_and_time_units():
    assert timestamp_unit(1770000000000,1780000000000)==1000
    with pytest.raises(ValueError): timestamp_unit(1770000000,1780000000000)
    p=parse_body('<msg><appmsg><title>回复</title><type>57</type><refermsg><svrid>123</svrid><content>原文</content></refermsg></appmsg></msg>',49)
    assert p['quote']['svrid']=='123' and p['type']=='引用'
    assert '未解析' in parse_body('<img/>',3)['text']
    p=parse_body('<msg><voicetrans transtext="这是一段已有转写"/></msg>',34)
    assert '已有转写' in p['text'] and 'transcription_source' in p
    assert decode_content(b'\x28\xb5\x2f\xfdBAD',4)[1]
    with pytest.raises(ValueError):time_window(start='2026-09-19')
    with pytest.raises(ValueError):time_window(start='2026-09-20',end='2026-09-19')


def test_conflicting_duplicate_fails():
    row={'local_id':1,'server_id':3,'create_time':1770000000,'real_sender_id':1,'local_type':1,'message_content':'甲'}
    a=normalize(row,'a.db','g@chatroom',1,{1:'alice'})
    b=normalize({**row,'message_content':'乙'},'b.db','g@chatroom',1,{1:'alice'})
    with pytest.raises(ValueError,match='不同内容'):deduplicate([a,b])
