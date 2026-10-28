"""Offline helpers for the inspected 2.32.0 serialized content formats.

Preserve untouched serialized entries and trailing fields byte for byte.
No server/database operations belong in these tools.
"""
import copy
from pathlib import Path
import struct
import tempfile


def string(value):
    b = value.encode('utf-8')
    return struct.pack('<i', len(b)) + b + bytes(-len(b) % 4)


class Reader:
    def __init__(self, data, pos=32):
        self.data, self.pos = data, pos

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError('Truncated serialized content')
        result = self.data[self.pos:self.pos+size]
        self.pos += size
        return result

    def integer(self):
        return struct.unpack('<i', self.take(4))[0]

    def count(self, maximum=10000):
        n = self.integer()
        if not 0 <= n <= maximum:
            raise ValueError('Unsupported serialized content count')
        return n

    def text(self):
        n = self.count(100000)
        b = self.take(n)
        self.take(-self.pos % 4)
        try:
            return b.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('Invalid serialized UTF-8') from exc


def parse_patterns(raw):
    r = Reader(raw)
    sets = []
    for _ in range(r.count()):
        d = {'key': r.text(), 'displayName': r.text()}
        r.take(8)  # type, randomColorType
        r.text()   # defaultColorKey
        r.take(20) # five serialized ints/bools
        r.text()   # defaultCustomRestrictionGroupKey
        d['count_offset'] = r.pos
        d['patterns'] = []
        for _ in range(r.count()):
            start = r.pos
            p = dict(key=r.text(), displayName=r.text(), itemKey=r.text())
            p['customParameters'] = [r.text() for _ in range(r.count())]
            p['customColorKey'] = r.text()
            p['stuffingCost'], p['paintCost'] = r.integer(), r.integer()
            p['customRestrictionGroupKey'] = r.text()
            p['raw'] = raw[start:r.pos]
            d['patterns'].append(p)
        d['end'] = r.pos
        sets.append(d)
    return sets, r.pos


def append_pattern(raw, dataset, key, name, item_key, parameters, color, cost):
    if not key or not name.strip() or cost < 0:
        raise ValueError('A key, display name and nonnegative cost are required')
    sets, _ = parse_patterns(raw)
    if any(p['key'] == key for d in sets for p in d['patterns']):
        raise ValueError(f'Pattern {key!r} already exists')
    matches = [d for d in sets if d['key'] == dataset]
    if len(matches) != 1:
        raise ValueError(f'Expected one pattern dataset {dataset!r}')
    d = matches[0]
    entry = (string(key) + string(name) + string(item_key) + struct.pack('<i', len(parameters))
             + b''.join(string(p) for p in parameters) + string(color)
             + struct.pack('<ii', cost, 0) + string(''))
    result = bytearray(raw[:d['end']] + entry + raw[d['end']:])
    struct.pack_into('<i', result, d['count_offset'], len(d['patterns']) + 1)
    return bytes(result)


def parse_audio(raw):
    r = Reader(raw)
    entries = []
    for _ in range(r.count()):
        start = r.pos
        d = dict(key=r.text(), title=r.text(), artist=r.text())
        d['clip'] = struct.unpack('<iq', r.take(12))
        r.take(4)  # volume
        r.take(12*r.count())  # tempo sections: start, BPM, beats
        r.take(4*r.count())   # playback markers
        r.take(4)            # trailing playback parameter
        d['raw'] = raw[start:r.pos]
        entries.append(d)
    return entries, r.pos


def append_audio(raw, key, name, artist, file_id, clip_id, duration):
    entries, end = parse_audio(raw)
    if any(d['key'] == key for d in entries):
        raise ValueError(f'Audio key {key!r} already exists')
    if duration <= 0:
        raise ValueError('Audio must contain samples')
    # No fabricated beat grid/markers: ordinary continuous playback.
    entry = (string(key) + string(name) + string(artist)
             + struct.pack('<iqf', file_id, clip_id, .8) + struct.pack('<iif', 0, 0, 0))
    result = bytearray(raw[:end] + entry + raw[end:])
    struct.pack_into('<i', result, 32, len(entries)+1)
    return bytes(result), len(entries)


def resolve(owner, ref):
    fid, pid = ref['m_FileID'], ref['m_PathID']
    if fid == 0:
        return owner.assets_file.objects[pid]
    name = Path(owner.assets_file.externals[fid-1].path).name
    return next(a for a in owner.assets_file.environment.assets if a.name == name).objects[pid]


def script_name(obj):
    raw = obj.get_raw_data()
    if len(raw) < 28:
        return ''
    fid, pid = struct.unpack_from('<iq', raw, 16)
    return resolve(obj, {'m_FileID': fid, 'm_PathID': pid}).read().m_ClassName


def manager(env, name):
    matches = [o for o in env.objects if o.type.name == 'MonoBehaviour' and script_name(o) == name]
    if len(matches) != 1:
        raise ValueError(f'Expected one {name}, found {len(matches)}')
    return matches[0]


def clone(obj, pid):
    result = copy.copy(obj)
    result.path_id = pid
    obj.assets_file.objects[pid] = result
    return result


def pptr(pid, fid=0):
    return {'m_FileID': fid, 'm_PathID': pid}


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(data)
            f.flush()
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def parse_media(raw):
    r = Reader(raw)
    entries = []
    for _ in range(r.count()):
        entries.append(dict(key=r.text(), audio_id=r.integer(),
                            art=struct.unpack('<iq', r.take(12))))
    return entries, r.pos


def append_media(raw, key, audio_id, file_id, art_id):
    entries, end = parse_media(raw)
    if any(d['key'] == key for d in entries):
        raise ValueError(f'Record key {key!r} already exists')
    result = bytearray(raw[:end] + string(key) + struct.pack('<iiq', audio_id, file_id, art_id) + raw[end:])
    struct.pack_into('<i', result, 32, len(entries)+1)
    return bytes(result), len(entries)
