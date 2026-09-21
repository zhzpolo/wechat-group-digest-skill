"""SQLCipher 4 page reader; adapted from Apache-2.0 wx-cli (see NOTICE).

No plaintext database is ever written. Every requested page must pass HMAC.
WAL is checked as a rolling chain, and only committed frames are visible.
"""
import hashlib
import hmac
import struct
from Crypto.Cipher import AES

PAGE = 4096
RESERVE = 80
HEADER = b"SQLite format 3\0"


def mac_key(key, salt):
    return hashlib.pbkdf2_hmac('sha512', key, bytes(x ^ 0x3a for x in salt), 2, 32)


def verify_page(page, number, auth_key):
    start = 16 if number == 1 else 0
    if len(page) != PAGE:
        return False
    digest = hmac.digest(auth_key, page[start:-64] + struct.pack('<I', number), 'sha512')
    return hmac.compare_digest(digest, page[-64:])


def valid_key(key, page):
    return verify_page(page, 1, mac_key(key, page[:16]))


def decrypt_page(page, number, key, auth_key):
    if not verify_page(page, number, auth_key):
        raise ValueError(f'数据库页 {number} HMAC 校验失败；拒绝返回不可信记录')
    start = 16 if number == 1 else 0
    plain = (HEADER if start else b'') + AES.new(key, AES.MODE_CBC, page[-80:-64]).decrypt(page[start:-80]) + bytes(80)
    if number == 1:
        if plain[16:18] != b'\x10\x00' or plain[20:24] != bytes([80, 64, 32, 32]):
            raise ValueError('未知 SQLite/SQLCipher 页结构')
        # WAL has already been folded into the immutable virtual view.
        plain = plain[:18] + b'\x01\x01' + plain[20:]
    return plain


def checksum(data, state=(0, 0), endian='<'):
    if len(data) % 8:
        raise ValueError('WAL 校验输入长度错误')
    a, b = state
    for x, y in struct.iter_unpack(endian + 'II', data):
        a = (a + x + b) & 0xffffffff
        b = (b + y + a) & 0xffffffff
    return a, b


def wal_index(data):
    """Return committed page offsets and final DB size; ignore stale/torn tail."""
    meta = {'bytes': len(data), 'valid_frames': 0, 'committed_frames': 0, 'tail_reason': None}
    if not data:
        return {}, None, meta
    if len(data) < 32:
        raise ValueError('WAL 头不完整，需重新获取快照')
    magic, version, pagesize = struct.unpack('>III', data[:12])
    if magic not in (0x377f0682, 0x377f0683) or version != 3007000 or pagesize != PAGE:
        raise ValueError('不支持的 WAL 格式')
    endian = '<' if magic == 0x377f0682 else '>'
    state = checksum(data[:24], endian=endian)
    if state != struct.unpack('>II', data[24:32]):
        raise ValueError('WAL 头校验失败')
    pending, committed, size = {}, {}, None
    step = 24 + PAGE
    for offset in range(32, len(data), step):
        frame = data[offset:offset + step]
        if len(frame) != step:
            meta['tail_reason'] = 'incomplete_tail'
            break
        number, commit_size = struct.unpack('>II', frame[:8])
        if frame[8:16] != data[16:24]:
            meta['tail_reason'] = 'stale_salt_tail'
            break
        expected = checksum(frame[:8] + frame[24:], state, endian)
        if expected != struct.unpack('>II', frame[16:24]) or not number:
            meta['tail_reason'] = 'invalid_checksum_tail'
            break
        state = expected
        meta['valid_frames'] += 1
        pending[number] = offset + 24
        if commit_size:
            committed.update(pending)
            pending.clear()
            size = commit_size
            committed = {p: o for p, o in committed.items() if p <= size}
            meta['committed_frames'] = meta['valid_frames']
    meta['uncommitted_frames'] = meta['valid_frames'] - meta['committed_frames']
    return committed, size, meta
