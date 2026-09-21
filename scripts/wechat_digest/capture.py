"""Optional short-lived opening-time capture, informed by ChatTrace (MIT).

No key file, memory dump, force-exit, spawned instance or credential logging.
Requires an explicit --capture invocation and a hash-bound, inspected anchor.
"""
import hashlib
import json
from pathlib import Path
import queue
import time
from datetime import datetime, timezone
from .crypto import valid_key


def derive_verified(candidate, pages):
    result = {}
    try:
        for name, page in pages.items():
            key = bytearray(hashlib.pbkdf2_hmac('sha512', candidate, page[:16], 256000, 32))
            if not valid_key(key, page):
                key[:] = bytes(32)
                return None
            result[name] = key
        return result
    finally:
        if len(result) != len(pages):
            for key in result.values(): key[:] = bytes(len(key))


def collect_verified(candidate, pages, found):
    """Accept each DB independently: an argument may be a raw per-DB key.

    Both raw and PBKDF2 forms are sourced in the reviewed implementations.
    Neither is accepted without its own DB-page HMAC, never by byte appearance.
    """
    for name,page in pages.items():
        if name in found: continue
        if valid_key(candidate,page):
            found[name]=bytearray(candidate)
            continue
        derived=bytearray(hashlib.pbkdf2_hmac('sha512',candidate,page[:16],256000,32))
        if valid_key(derived,page): found[name]=derived
        else: derived[:]=bytes(len(derived))


def capture_keys(pid, pages, anchor_file, seconds=120, status_file=None, wait_for_close=False):
    import frida
    metadata = json.loads(Path(anchor_file).read_text(encoding='utf-8'))
    dll = Path(metadata['dll'])
    if hashlib.sha256(dll.read_bytes()).hexdigest() != metadata['dll_sha256']:
        raise ValueError('微信 DLL 已变化；拒绝使用旧偏移，需重新静态检查')
    anchors = metadata['candidates']
    if len(anchors) != 1:
        raise ValueError('开库函数候选不唯一，不能自动捕获')
    anchor = anchors[0]
    incoming = queue.Queue(maxsize=256)
    found={}
    succeeded=False
    state = {'armed':False,'error':False,'candidates_seen':0,'candidates_checked':0,'last_argument_length':None,'verified_databases':0,'verified_names':[]}
    status_path = Path(status_file) if status_file else Path(__file__).resolve().parent.parent / 'local-capture-status.json'
    def status(stage):
        status_path.write_text(json.dumps({'stage':stage,'pid':pid,'updated_at':datetime.now(timezone.utc).isoformat(),
                                          'seconds':seconds,**state},ensure_ascii=False,indent=2),encoding='utf-8')
    session = script = None
    # ChatTrace agent.py checks aligned 32-byte windows within the first 128
    # bytes of RCX. This transient, bounded candidate block is never logged or
    # persisted. No arbitrary register memory dump is collected.
    js = r'''
const cfg = CONFIG;
let armed=false;
let timer=null;
function tryArm(){
if(armed)return;
const m = Process.findModuleByName('Weixin.dll');
if(!m)return;
armed=true;
if(timer!==null)clearInterval(timer);
if (m.path.toLowerCase().replace(/\\/g,'/') !== cfg.path.toLowerCase().replace(/\\/g,'/')) {
  send({event:'error'});
} else {
  const at = m.base.add(cfg.rva);
  const bytes = new Uint8Array(at.readByteArray(cfg.prefix.length/2));
  const actual = Array.from(bytes).map(x=>x.toString(16).padStart(2,'0')).join('');
  if (actual !== cfg.prefix) { send({event:'error'}); }
  else {
    let seen=new Set();
    const listener=Interceptor.attach(at,{onEnter:function(){
      if(seen.size>=256)return;
      try {
        const len=this.context.rdx.toUInt32();
        const value=this.context.rcx.readByteArray(len===32 ? 32 : 128);
        const identity=Array.from(new Uint8Array(value)).join(',');
        if(seen.has(identity))return;
        seen.add(identity);
        send({event:'candidate',length:len},value);
      } catch(e) {}
    }});
    send({event:'armed'});
  }
}
}
tryArm();
if(!armed)timer=setInterval(tryArm,10);
'''.replace('CONFIG',json.dumps({'path':str(dll),'rva':anchor['entry_rva'],'prefix':anchor['entry_bytes']}))
    def on_message(message, data):
        if message.get('type') == 'error':
            state['error'] = True
            return
        event = message.get('payload',{}).get('event')
        if event == 'armed': state['armed'] = True
        elif event == 'error': state['error'] = True
        elif event == 'candidate' and data and len(data) in (32,128):
            state['candidates_seen'] += 1
            state['last_argument_length'] = message['payload'].get('length')
            try: incoming.put_nowait(bytearray(data))
            except queue.Full: pass
    try:
        if wait_for_close:
            from .windows import processes
            exe=dll.parent.parent/'Weixin.exe'
            if not exe.is_file(): raise ValueError('无法验证微信启动程序位置')
            status('waiting_for_user_to_exit_weixin')
            print('等待从托盘完整退出微信；检测到退出后将自动启动微信并布置捕获。',flush=True)
            close_deadline=time.monotonic()+3600
            while processes():
                if time.monotonic()>close_deadline:
                    raise RuntimeError('等待退出微信超时；尚未读取密钥')
                time.sleep(.5)
            pid=frida.spawn(str(exe),stdio='pipe')
            status('spawned_suspended')
        status('attaching')
        session=frida.attach(pid)
        script=session.create_script(js)
        script.on('message',on_message)
        script.load()
        if wait_for_close:
            frida.resume(pid)
            print('微信已自动启动。请在新窗口登录原账号，如需手机确认请本人完成。',flush=True)
        deadline=time.monotonic()+seconds
        announced=False
        while time.monotonic()<deadline:
            if state['error']:
                raise RuntimeError('运行时模块/函数校验或捕获失败；未接受任何密钥')
            if state['armed'] and not announced:
                if wait_for_close:
                    print('启动阶段捕获已就绪，正在等待登录开库；密钥不会保存。',flush=True)
                else:
                    print('捕获已就绪：请在微信内退出登录并重新登录同一账号（不要从托盘关闭程序）。密钥不会保存。',flush=True)
                status('armed_waiting_for_database_open')
                announced=True
            try: candidate=incoming.get(timeout=.2)
            except queue.Empty: continue
            try:
                for offset in range(0,len(candidate)-31,8):
                    state['candidates_checked'] += 1
                    status('checking_candidate')
                    collect_verified(candidate[offset:offset+32],pages,found)
                    state['verified_databases']=len(found)
                    state['verified_names']=list(found)
                    if len(found)==len(pages):
                        succeeded=True
                        status('verified')
                        print('全部所需数据库密钥已通过 HMAC；结束捕获，开始只读查询。',flush=True)
                        return found
            finally:
                candidate[:]=bytes(len(candidate))
        status('timed_out')
        raise RuntimeError(f'捕获超时，检查了 {state["candidates_checked"]} 个候选但未取得通过校验的密钥；没有生成真实聊天报告')
    except BaseException:
        status('failed_or_interrupted')
        raise
    finally:
        if not succeeded:
            for key in found.values(): key[:]=bytes(len(key))
        if script is not None:
            try: script.unload()
            except Exception: pass
        if session is not None:
            try: session.detach()
            except Exception: pass
        while not incoming.empty():
            candidate=incoming.get_nowait();candidate[:]=bytes(len(candidate))
