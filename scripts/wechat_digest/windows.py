"""Windows read-only process inspection. No DLL injection, key files or dumps.

Raw-key pattern strategy adapted from wx-cli, Apache-2.0; see NOTICE.
"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import re
import psutil
from .crypto import valid_key


def processes():
    result = []
    for p in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
        try:
            if (p.info['name'] or '').lower() == 'weixin.exe' and not any('--type=' in s for s in (p.info['cmdline'] or [])):
                result.append(p.info)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    return result


def accounts():
    roots = {Path.home() / 'Documents' / 'xwechat_files'}
    conf = Path(os.environ.get('APPDATA', '')) / 'Tencent/xwechat/config'
    for file in conf.glob('*.ini'):
        raw = file.read_bytes()
        for encoding in ('utf-8', 'utf-16', 'gb18030'):
            try:
                path = Path(raw.decode(encoding).strip('\x00\r\n '))
                if path.is_dir():
                    roots.add(path / 'xwechat_files')
                    roots.add(path)
                    break
            except (UnicodeError, OSError, ValueError):
                pass
    return sorted({p.resolve() for root in roots if root.is_dir() for p in root.glob('*/db_storage')})


class MBI(C.Structure):
    _fields_ = [('base', C.c_void_p), ('allocation', C.c_void_p), ('allocation_protect', W.DWORD),
                ('region_size', C.c_size_t), ('state', W.DWORD), ('protect', W.DWORD), ('type', W.DWORD)]


def extract_keys(pid, pages):
    """Only retain keys verified against requested databases; never print secrets."""
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t]
    kernel.VirtualQueryEx.restype = C.c_size_t
    kernel.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
    kernel.ReadProcessMemory.restype = W.BOOL
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise PermissionError('无法只读访问微信进程；请在同用户、同权限终端运行，必要时使用管理员终端')
    found = {}
    salt_map = {page[:16]: name for name, page in pages.items()}
    pattern = re.compile(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'")
    address, mbi, succeeded = 0, MBI(), False
    try:
        while kernel.VirtualQueryEx(handle, address, C.byref(mbi), C.sizeof(mbi)):
            base, length = mbi.base or 0, mbi.region_size
            if mbi.state == 0x1000 and not mbi.protect & 0x100 and (mbi.protect & 0xff) in (2, 4, 8, 0x20, 0x40, 0x80):
                offset = 0
                while offset < length:
                    amount = min(2 * 1024 * 1024, length - offset)
                    buf, n = C.create_string_buffer(amount), C.c_size_t()
                    kernel.ReadProcessMemory(handle, base + offset, buf, amount, C.byref(n))
                    raw = buf.raw[:n.value]
                    for match in pattern.finditer(raw):
                        salt = bytes.fromhex(match[2].decode('ascii'))
                        name = salt_map.get(salt)
                        if name and name not in found:
                            key = bytearray.fromhex(match[1].decode('ascii'))
                            if valid_key(key, pages[name]):
                                found[name] = key
                            else:
                                key[:] = bytes(len(key))
                    # Upstream scanner/mod.rs collect_salt_adjacent_keys strategy.
                    # These are bounded candidate layouts, never assumed keys;
                    # only an exact database-page HMAC match is accepted.
                    for salt, name in salt_map.items():
                        if name in found:
                            continue
                        start = 0
                        while (position := raw.find(salt, start)) >= 0:
                            offsets = [position - n for n in (32, 40, 48)] + [position + 16 + n for n in (0, 8, 16)]
                            for candidate in offsets:
                                if candidate < 0 or candidate + 32 > len(raw):
                                    continue
                                key = bytearray(raw[candidate:candidate+32])
                                if valid_key(key, pages[name]):
                                    found[name] = key
                                    break
                                key[:] = bytes(len(key))
                            if name in found:
                                break
                            start = position + 1
                    C.memset(buf, 0, amount)
                    del raw, buf
                    if len(found) == len(pages):
                        succeeded = True
                        return found
                    offset += max(1, amount - 100) if offset + amount < length else amount
            nxt = base + length
            if nxt <= address:
                break
            address = nxt
        missing = sorted(set(pages) - set(found))
        if missing:
            for key in found.values():
                key[:] = bytes(len(key))
            raise RuntimeError('没有找到通过 HMAC 的密钥：' + ', '.join(missing) + '。请在微信打开目标群后重试；不使用猜测偏移或未经验证的 hook。')
        succeeded = True
        return found
    finally:
        if not succeeded:
            for key in found.values():
                key[:] = bytes(len(key))
        kernel.CloseHandle(handle)
