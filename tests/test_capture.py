import hashlib
import os
import struct
import sys
import types
import json
import pytest
from wechat_digest.capture import derive_verified, collect_verified, capture_keys
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
