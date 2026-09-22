import hashlib
import os
import struct
import sys
import types
import json
import pytest
from wechat_digest.capture import derive_verified, collect_verified, capture_keys
from wechat_digest import reader
from test_crypto import encrypt


def test_master_key_derivation_is_bound_to_each_database():
    master=os.urandom(32)
    pages={}
    for name in ('contact.db','message_0.db'):
        salt=os.urandom(16)
        key=hashlib.pbkdf2_hmac('sha512',master,salt,256000,32)
        pages[name]=encrypt(bytes(4096),1,key,salt)
    keys=derive_verified(master,pages)
    assert set(keys)==set(pages)
    assert keys['contact.db']!=keys['message_0.db']
    assert derive_verified(os.urandom(32),pages) is None
    for key in keys.values(): key[:]=bytes(len(key))


def test_capture_accumulates_independent_raw_database_keys():
    pages={}; expected={}; found={}
    for name in ('contact.db','message_0.db'):
        key=os.urandom(32);salt=os.urandom(16)
        expected[name]=key
        pages[name]=encrypt(bytes(4096),1,key,salt)
    collect_verified(expected['contact.db'],pages,found)
    assert set(found)=={'contact.db'}
    collect_verified(expected['message_0.db'],pages,found)
    assert found==expected
    for key in found.values():key[:]=bytes(len(key))


def test_capture_exception_unloads_and_detaches(tmp_path,monkeypatch):
    dll=tmp_path/'Weixin.dll';dll.write_bytes(b'fictional dll')
    anchors=tmp_path/'anchors.json'
    anchors.write_text(json.dumps({'dll':str(dll),'dll_sha256':hashlib.sha256(dll.read_bytes()).hexdigest(),
                                  'candidates':[{'entry_rva':1,'entry_bytes':'00'}]}))
    calls=[]
    class Script:
        def on(self,event,callback): self.callback=callback
        def load(self): self.callback({'payload':{'event':'error'}},None)
        def unload(self): calls.append('unloaded')
    class Session:
        def create_script(self,code): return Script()
        def detach(self): calls.append('detached')
    monkeypatch.setitem(sys.modules,'frida',types.SimpleNamespace(attach=lambda pid:Session()))
    with pytest.raises(RuntimeError,match='校验'):
        capture_keys(1,{},anchors,1,status_file=tmp_path/'status.json')
    assert calls==['unloaded','detached']
    assert sorted(p.name for p in tmp_path.iterdir())==['Weixin.dll','anchors.json','status.json']


def test_changed_binary_rejects_before_attach(tmp_path,monkeypatch):
    dll=tmp_path/'Weixin.dll';dll.write_bytes(b'changed')
    anchors=tmp_path/'anchors.json';anchors.write_text(json.dumps({'dll':str(dll),'dll_sha256':'bad'}))
    monkeypatch.setitem(sys.modules,'frida',types.SimpleNamespace(attach=lambda pid:pytest.fail('must not attach')))
    with pytest.raises(ValueError,match='已变化'):capture_keys(1,{},anchors)


def test_online_snapshot_success_never_requests_restart(monkeypatch):
    events=[]
    expected_keys={'contact.db':bytearray(b'k'*32)}
    expected_snapshot={'snapshot':'online'}
    monkeypatch.setattr(reader,'snapshots',lambda paths,attempts:(events.append(('snapshot',attempts)) or expected_snapshot))
    monkeypatch.setattr(reader,'extract_keys',lambda pid,pages:(events.append(('scan',pid)) or expected_keys))
    monkeypatch.setattr(reader,'capture_keys',lambda *a,**k:pytest.fail('online success must not capture'))
    keys,copies,audit=reader.acquire_access(['db'],{'contact.db':b'page'},[{'pid':7}],
                                             capture='anchor.json',restart_fallback=True,online_attempts=4)
    assert keys is expected_keys and copies is expected_snapshot
    assert events==[('snapshot',4),('scan',7)]
    assert audit=={'mode':'online','online_attempts':4,'restart_required':False,'fallback_reason':None}
    reader._wipe(keys)


def test_restart_fallback_only_after_bounded_online_failure(monkeypatch):
    events=[]
    fallback_keys={'contact.db':bytearray(b'z'*32)}
    def snap(paths,attempts):
        events.append(('snapshot',attempts))
        if len(events)==1: raise RuntimeError('changing')
        return {'snapshot':'after-restart'}
    monkeypatch.setattr(reader,'snapshots',snap)
    monkeypatch.setattr(reader,'extract_keys',lambda *a:pytest.fail('unstable snapshot must not scan keys'))
    def capture(pid,pages,anchor,seconds,wait_for_close):
        events.append(('capture',wait_for_close))
        return fallback_keys
    monkeypatch.setattr(reader,'capture_keys',capture)
    keys,copies,audit=reader.acquire_access(['db'],{'contact.db':b'page'},[{'pid':9}],
                                             capture='anchor.json',capture_seconds=5,
                                             restart_fallback=True,online_attempts=3)
    assert events==[('snapshot',3),('capture',True),('snapshot',3)]
    assert copies=={'snapshot':'after-restart'} and keys is fallback_keys
    assert audit['mode']=='startup_capture_fallback' and audit['restart_required'] is True
    assert '连续 3 次' in audit['fallback_reason']
    reader._wipe(keys)


def test_stable_online_snapshot_with_missing_keys_uses_fallback(monkeypatch):
    events=[];fallback_keys={'contact.db':bytearray(b'y'*32)}
    monkeypatch.setattr(reader,'snapshots',lambda paths,attempts:(events.append('snapshot') or {'stable':True}))
    def missing(pid,pages):
        events.append('scan')
        raise RuntimeError('missing verified keys')
    monkeypatch.setattr(reader,'extract_keys',missing)
    monkeypatch.setattr(reader,'capture_keys',lambda *a,**k:(events.append('capture') or fallback_keys))
    keys,copies,audit=reader.acquire_access(['db'],{'contact.db':b'page'},[{'pid':5}],
                                             capture='anchor.json',restart_fallback=True,online_attempts=3)
    assert events==['snapshot','scan','capture','snapshot']
    assert audit['mode']=='startup_capture_fallback'
    assert 'HMAC' in audit['fallback_reason']
    reader._wipe(keys)


def test_failed_post_restart_snapshot_wipes_captured_keys(monkeypatch):
    key=bytearray(b'x'*32);calls=0
    def snap(paths,attempts):
        nonlocal calls
        calls+=1
        raise RuntimeError('unstable')
    monkeypatch.setattr(reader,'snapshots',snap)
    monkeypatch.setattr(reader,'capture_keys',lambda *a,**k:{'contact.db':key})
    with pytest.raises(RuntimeError,match='unstable'):
        reader.acquire_access(['db'],{'contact.db':b'page'},[{'pid':2}],capture='anchor.json',
                              restart_fallback=True,online_attempts=2)
    assert calls==2 and key==bytes(32)


def test_online_failure_without_fallback_does_not_capture(monkeypatch):
    monkeypatch.setattr(reader,'snapshots',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('changing')))
    monkeypatch.setattr(reader,'capture_keys',lambda *a,**k:pytest.fail('fallback was not enabled'))
    with pytest.raises(RuntimeError,match='未启用启动捕获降级'):
        reader.acquire_access(['db'],{},[{'pid':1}],capture='anchor.json',restart_fallback=False,
                              online_attempts=2)
