"""Stable encrypted snapshots and a read-only, decrypt-on-demand SQLite VFS."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import struct
import time
import uuid
import apsw
from .crypto import PAGE, decrypt_page, mac_key, wal_index


def _read(path, limit=None):
    try:
        # Unbuffered is essential for SHM: a buffered 96-byte read may prefetch
        # across Windows SQLite byte-range locks starting at byte 120.
        with path.open('rb', buffering=0) as stream:
            return stream.read() if limit is None else stream.read(limit)
    except FileNotFoundError:
        return b''


def _stamp(paths):
    result = []
    for path in paths:
        try:
            s = path.stat()
            result.append((s.st_ino, s.st_size, s.st_mtime_ns))
        except FileNotFoundError:
            result.append(None)
    return result


def snapshots(paths, attempts=6):
    """Optimistic stable snapshot: two identical reads, stable stat and SHM headers.

    Never uses a torn snapshot silently. Not a global transaction across shards.
    All DB/WAL bytes stay encrypted in RAM; no copies are written to disk.
    """
    files = [p for db in paths for p in (db, Path(str(db) + '-wal'))]
    shms = [Path(str(db) + '-shm') for db in paths]
    for attempt in range(attempts):
        try:
            before = _stamp(files)
            shm_before = [_read(p,96) for p in shms]
            first = [_read(p) for p in files]
            equal = all(hashlib.sha256(_read(p)).digest() == hashlib.sha256(data).digest() for p, data in zip(files, first))
            stable = equal and before == _stamp(files) and shm_before == [_read(p,96) for p in shms]
        except PermissionError:
            time.sleep(.25)
            continue
        if stable:
            result = {}
            for i, db in enumerate(paths):
                base, wal = first[2*i:2*i+2]
                if not base or len(base) % PAGE:
                    raise ValueError('数据库大小不符合已验证页格式：' + db.name)
                overlay, size, info = wal_index(wal)
                shm = shm_before[i]
                if len(shm) == 96:
                    # Windows WAL-index is native little endian, two identical headers.
                    if shm[:48] != shm[48:96]:
                        break
                    if shm[12] == 1:
                        mx_frame = struct.unpack('<I', shm[16:20])[0]
                        if mx_frame and (len(wal) < 32 or shm[32:40] != wal[16:24] or mx_frame != info['committed_frames']):
                            break
                info.update({'database': db.name, 'snapshot_method': 'double_read_sha256_stat_shm',
                             'database_sha256': hashlib.sha256(base).hexdigest(),
                             'wal_sha256': hashlib.sha256(wal).hexdigest(), 'attempt': attempt + 1})
                result[db] = (base, wal, overlay, size, info)
            else:
                return result
        time.sleep(.15)
    raise RuntimeError('微信数据库持续变化，未取得通过一致性检查的快照；请稍候重试')


class MemoryFile:
    def __init__(self, view):
        self.view = view

    def xRead(self, amount, offset):
        result = bytearray()
        while amount:
            number, start = divmod(offset, PAGE)
            take = min(PAGE - start, amount)
            result.extend(self.view.page(number + 1)[start:start+take])
            amount -= take
            offset += take
        return bytes(result)

    def xFileSize(self):
        return self.view.size * PAGE

    def xClose(self): pass
    def xLock(self, level): pass
    def xUnlock(self, level): pass
    def xCheckReservedLock(self): return False
    def xFileControl(self, op, ptr): return False
    def xSectorSize(self): return PAGE
    def xDeviceCharacteristics(self): return apsw.SQLITE_IOCAP_IMMUTABLE
    def xWrite(self, data, offset): raise apsw.ReadOnlyError('只读内存视图')
    def xTruncate(self, size): raise apsw.ReadOnlyError('只读内存视图')
    def xSync(self, flags): pass


class PageView(apsw.VFS):
    def __init__(self, snapshot, key):
        self.base, self.wal, self.overlay, size, self.info = snapshot
        self.key = key
        self.auth = bytearray(mac_key(key, self.base[:16]))
        self.size = size or len(self.base) // PAGE
        self.read_pages = set()
        self.name = 'wechat_memory_' + uuid.uuid4().hex
        super().__init__(self.name, '')

    def xOpen(self, name, flags):
        if not flags[0] & apsw.SQLITE_OPEN_MAIN_DB:
            raise apsw.ReadOnlyError('不允许临时数据库或日志')
        flags[1] = apsw.SQLITE_OPEN_READONLY
        return MemoryFile(self)

    def xAccess(self, pathname, flags): return False

    def page(self, number):
        if number < 1 or number > self.size:
            raise apsw.IOError('数据库请求越过已验证快照边界')
        offset = self.overlay.get(number)
        data = self.wal[offset:offset+PAGE] if offset is not None else self.base[(number-1)*PAGE:number*PAGE]
        plain = decrypt_page(data, number, self.key, self.auth)
        self.read_pages.add(number)
        return plain

    def dispose(self):
        self.auth[:] = bytes(len(self.auth))
        self.key = None
        self.base = self.wal = b''
        self.overlay.clear()
        self.unregister()


@contextmanager
def database(snapshot, key):
    view, conn = PageView(snapshot, key), None
    try:
        conn = apsw.Connection('snapshot', flags=apsw.SQLITE_OPEN_READONLY, vfs=view.name)
        conn.execute('PRAGMA temp_store=MEMORY; PRAGMA query_only=ON; PRAGMA trusted_schema=OFF')
        conn.execute('SELECT count(*) FROM sqlite_master').fetchone()
        yield conn, view
    finally:
        if conn is not None:
            conn.close()
        view.info['authenticated_pages_read'] = len(view.read_pages)
        view.dispose()
