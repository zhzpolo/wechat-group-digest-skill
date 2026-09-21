import ctypes
import hashlib
import hmac
import os
import struct
import apsw
import pytest
from Crypto.Cipher import AES
from wechat_digest.crypto import PAGE, checksum, mac_key, valid_key, wal_index
from wechat_digest.storage import database, snapshots


def encrypt(page, number, key, salt):
    start = 16 if number == 1 else 0
    iv = os.urandom(16)
    payload = (salt if start else b'') + AES.new(key, AES.MODE_CBC, iv).encrypt(page[start:-80]) + iv
    return payload + hmac.digest(mac_key(key, salt), payload[start:] + struct.pack('<I', number), 'sha512')


def make_wal(frames):
    header = struct.pack('>IIII', 0x377f0682, 3007000, PAGE, 0) + os.urandom(8)
    state = checksum(header)
    result = header + struct.pack('>II', *state)
    for number, size, data in frames:
        prefix = struct.pack('>II', number, size)
        state = checksum(prefix + data, state)
        result += prefix + header[16:24] + struct.pack('>II', *state) + data
    return result


def test_real_sqlite_pages_wal_and_cleanup(tmp_path):
    path = tmp_path / 'synthetic.db'
    c = apsw.Connection(str(path))
    c.execute('PRAGMA page_size=4096')
    reserve = ctypes.c_int(80)
    assert c.file_control('main', apsw.SQLITE_FCNTL_RESERVE_BYTES, ctypes.addressof(reserve))
    c.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)')
    c.execute('INSERT INTO messages VALUES (1, ?)', ('虚构：建议周一开会，不代表已决定',))
    base = path.read_bytes()
    c.execute('INSERT INTO messages VALUES (2, ?)', ('虚构：收到，不代表同意',))
    updated = path.read_bytes()
    c.close()
    key, salt = bytearray(os.urandom(32)), os.urandom(16)
    encoded = b''.join(encrypt(base[i:i+PAGE], i//PAGE+1, key, salt) for i in range(0, len(base), PAGE))
    assert valid_key(key, encoded[:PAGE])
    assert not valid_key(os.urandom(32), encoded[:PAGE])
    path.write_bytes(encoded)
    frames = [(i//PAGE+1, len(updated)//PAGE if i+PAGE == len(updated) else 0,
               encrypt(updated[i:i+PAGE], i//PAGE+1, key, salt)) for i in range(0, len(updated), PAGE)]
    wal = make_wal(frames + [(2, 0, encrypt(base[PAGE:2*PAGE], 2, key, salt))])
    (tmp_path / 'synthetic.db-wal').write_bytes(wal)
    snap = snapshots([path])[path]
    with database(snap, key) as (db, view):
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 2
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        with pytest.raises(apsw.ReadOnlyError):
            db.execute('DELETE FROM messages')
    assert view.key is None and not view.base
    assert path.read_bytes() == encoded
    assert len(list(tmp_path.iterdir())) == 2
    with pytest.raises(RuntimeError,match='synthetic failure'):
        with database(snapshots([path])[path],key) as (db,failed_view):
            db.execute('SELECT * FROM messages').fetchall()
            raise RuntimeError('synthetic failure')
    assert failed_view.key is None and not failed_view.base
    assert len(list(tmp_path.iterdir())) == 2
    overlay, size, meta = wal_index(wal)
    assert meta['uncommitted_frames'] == 1 and size == len(updated)//PAGE
    corrupt = wal[:32+len(frames)*(PAGE+24)] + b'bad'
    assert wal_index(corrupt)[2]['tail_reason'] == 'incomplete_tail'


def test_tampered_page_rejected(tmp_path):
    from wechat_digest.crypto import decrypt_page
    key, salt = os.urandom(32), os.urandom(16)
    page = bytearray(encrypt(bytes(PAGE), 2, key, salt))
    page[50] ^= 1
    with pytest.raises(ValueError, match='HMAC'):
        decrypt_page(page, 2, key, mac_key(key, salt))


def test_wal_stops_at_bad_checksum():
    wal = bytearray(make_wal([(2, 2, bytes(PAGE)), (3, 3, bytes(PAGE))]))
    wal[-10] ^= 1
    overlay, size, meta = wal_index(wal)
    assert size == 2 and set(overlay) == {2}
    assert meta['tail_reason'] == 'invalid_checksum_tail'
