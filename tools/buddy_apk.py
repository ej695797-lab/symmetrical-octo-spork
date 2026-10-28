"""Patch only the inspected live79 build; never guess offsets for another APK."""
import hashlib
import re
import struct
from urllib.parse import urlsplit

VERSION = "2.32.0-buddy"
OLD_PACKAGE = 'com.TrassGames.Yeeps'
PACKAGE = 'com.BuddyGames.Buddy'
APP_LABEL = 'Yeeps Buddy'
SOURCE_APK_SHA256 = "21e203194d07a20a68f37b4d34d74ab2c34e94077f38ae26d9caf90c8e37a905"
SOURCE_NATIVE_SHA256 = "235da516efbb5ce7327d68477426eccccefb418717ad064c5c5fbaa5b3d65fe2"
GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


def validate_settings(server: str, realtime: str, voice: str) -> None:
    url = urlsplit(server)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username
            or url.password or url.path or url.query or url.fragment
            or any(ord(c) <= 32 or c.isspace() for c in server)):
        raise ValueError("Server must be an http(s) base URL without credentials, path or query")
    host = url.hostname.lower().rstrip('.')
    if host == 'live.yeeps-tools.org' or host.endswith('.live.yeeps-tools.org'):
        raise ValueError("Use your own backend host, not the source APK author's server")
    try:
        url.port
    except ValueError as exc:
        raise ValueError("Invalid server port") from exc
    if not GUID.fullmatch(realtime) or not GUID.fullmatch(voice):
        raise ValueError("Both Photon App IDs must be UUIDs")


class Elf:
    """The small ELF64 subset needed to inspect symbols and RELATIVE pointers."""
    def __init__(self, data: bytes):
        if data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
            raise ValueError("Expected a little-endian AArch64 ELF64")
        self.data = data
        self.phoff = struct.unpack_from('<Q', data, 32)[0]
        self.shoff = struct.unpack_from('<Q', data, 40)[0]
        self.phsize, self.phnum, self.shsize, self.shnum = struct.unpack_from('<4H', data, 54)
        self.programs = [struct.unpack_from('<II6Q', data, self.phoff + i * self.phsize)
                         for i in range(self.phnum)]
        self.sections = [struct.unpack_from('<II4QII2Q', data, self.shoff + i * self.shsize)
                         for i in range(self.shnum)]
        self.symbols = {}
        self.relocations = {}
        for section in self.sections:
            if section[1] == 2:  # SHT_SYMTAB
                strings = self.sections[section[6]]
                pool = data[strings[4]:strings[4] + strings[5]]
                for off in range(section[4], section[4] + section[5], section[9]):
                    name, info, other, index, value, size = struct.unpack_from('<IBBHQQ', data, off)
                    name = pool[name:pool.find(b'\0', name)].decode()
                    if name:
                        self.symbols.setdefault(name, (value, size))
            if section[1] == 4:  # SHT_RELA
                for off in range(section[4], section[4] + section[5], section[9]):
                    target, info, addend = struct.unpack_from('<QQq', data, off)
                    if info & 0xffffffff == 1027:  # R_AARCH64_RELATIVE
                        self.relocations[target] = (off, addend)

    def offset(self, address: int) -> int:
        for h in self.programs:
            if h[0] == 1 and h[3] <= address < h[3] + h[5]:
                return h[2] + address - h[3]
        raise ValueError(f"Unmapped ELF address {address:#x}")

    def section(self, name: str):
        pool = self.sections[struct.unpack_from('<H', self.data, 62)[0]]
        strings = self.data[pool[4]:pool[4] + pool[5]]
        for section in self.sections:
            start = section[0]
            if strings[start:strings.index(b'\0', start)].decode() == name:
                return section
        raise ValueError(f'Missing ELF section {name}')

    def pointer_string(self, symbol: str) -> str:
        _, address = self.relocations[self.symbols[symbol][0]]
        offset = self.offset(address)
        return self.data[offset:self.data.index(b'\0', offset)].decode()


def patch_native(data: bytes, server: str, realtime: str, voice: str) -> bytes:
    if hashlib.sha256(data).hexdigest() != SOURCE_NATIVE_SHA256:
        raise ValueError("Unsupported native library: expected the inspected live79 build")
    validate_settings(server, realtime, voice)
    elf = Elf(data)
    original_ids = {elf.pointer_string(name).lower() for name in
                    ['_ZL15kOurPhotonAppId', '_ZL14kOurVoiceAppId']}
    if realtime.lower() in original_ids or voice.lower() in original_ids:
        raise ValueError('Use host-owned Photon IDs, not the source APK author\'s apps')
    result = bytearray(data)
    page = 0x4000  # A new segment must also work on 16 KB-page Android devices.
    align = lambda n: (n + page - 1) & -page
    file_start = align(len(result))
    from buddy_ovr import profile_locations, build_profile
    locations = profile_locations(elf)
    address_start = locations['base']
    strings = bytearray(build_profile(elf, result, locations))
    replacements = {
        '_ZL9kSelfBase': server + '/xxxxxxxxxxxxxxxxxx',
        '_ZL11kDevlogHost': urlsplit(server).hostname,
        '_ZL8kCosHost': urlsplit(server).hostname,
        '_ZL15kOurPhotonAppId': realtime,
        '_ZL14kOurVoiceAppId': voice,
        '_ZL8kBuildId': 'buddy1',
    }
    for symbol, value in replacements.items():
        location = elf.symbols[symbol][0]
        reloc_offset, old_address = elf.relocations[location]
        new_address = address_start + len(strings)
        strings += value.encode() + b'\0'
        struct.pack_into('<q', result, reloc_offset + 16, new_address)
        struct.pack_into('<Q', result, elf.offset(location), new_address)
        # Remove the old host/app ID storage too; unexpected old references
        # must fail locally rather than contacting the author's services.
        if symbol != '_ZL8kBuildId':
            old_offset = elf.offset(old_address)
            old_length = len(elf.pointer_string(symbol).encode())
            result[old_offset:old_offset + old_length] = b'\0' * old_length

    # Preserve game startup/validation/Photon hooks and the Explorer. These
    # optional installers otherwise start the removed feed/video/cosmetic APIs.
    for symbol in ['_ZL19InstallRemoteClientv', '_ZL17InstallRemoteLogsv',
                   '_ZL18DevlogUploadThreadv', '_ZL11DevlogFlushv']:
        offset = elf.offset(elf.symbols[symbol][0])
        result[offset:offset + 4] = bytes.fromhex('c0035fd6')  # RET, void function
    if result.count(b'\0-blaze\0') != 1:
        raise ValueError("Unexpected native version-suffix layout")
    # Unity PlayerSettings now carries the complete -buddy version. The old
    # label hook appends this suffix unconditionally, so make it empty.
    result = result.replace(b'\0-blaze\0', b'\0' * 8)

    # Reuse PT_NOTE for generated code/strings. Extend only the existing RW
    # zero-fill area for original-function slots; never map debug data into BSS.
    notes = [i for i, h in enumerate(elf.programs) if h[0] == 4]
    if len(notes) != 1:
        raise ValueError("Unexpected ELF program-header layout")
    programs = list(elf.programs)
    writable = list(programs[locations['writable_index']])
    writable[6] = locations['memory_end'] - writable[3]
    programs[locations['writable_index']] = tuple(writable)
    programs[notes[0]] = (1, 5, file_start, address_start, address_start,
                          len(strings), len(strings), page)
    phdrs = [h for h in programs if h[0] == 6]
    loads = sorted((h for h in programs if h[0] == 1), key=lambda h: h[3])
    rest = [h for h in programs if h[0] not in (1, 6)]
    for i, h in enumerate(phdrs + loads + rest):
        struct.pack_into('<II6Q', result, elf.phoff + i * elf.phsize, *h)
    result += b'\0' * (file_start - len(result)) + strings
    if len(OLD_PACKAGE) != len(PACKAGE):
        raise ValueError('Native package replacement must preserve string lengths')
    result = result.replace(OLD_PACKAGE.encode(), PACKAGE.encode())
    if b'live.yeeps-tools.org' in result:
        raise ValueError("Author backend still present in patched native library")
    return bytes(result)


class AndroidXml:
    """Edit string values/booleans without rebuilding Android resources."""
    def __init__(self, data: bytes):
        if data[:4] != b'\x03\0\x08\0':
            raise ValueError("Expected Android binary XML")
        self.chunks = []
        pos = 8
        while pos < len(data):
            kind, header, size = struct.unpack_from('<HHI', data, pos)
            if size < header or pos + size > len(data):
                raise ValueError("Malformed Android XML chunk")
            self.chunks.append(bytearray(data[pos:pos + size]))
            pos += size
        pool = next(c for c in self.chunks if struct.unpack_from('<H', c)[0] == 1)
        self.pool = pool
        self.strings = []
        count, styles, flags, start, style_start = struct.unpack_from('<5I', pool, 8)
        header = struct.unpack_from('<H', pool, 2)[0]
        for i in range(count):
            at = start + struct.unpack_from('<I', pool, header + 4 * i)[0]
            if flags & 256:
                _, at = self._length(pool, at, 1)
                length, at = self._length(pool, at, 1)
                value = pool[at:at + length].decode('utf-8')
            else:
                length, at = self._length(pool, at, 2)
                value = pool[at:at + 2 * length].decode('utf-16le')
            self.strings.append(value)

    @staticmethod
    def _length(data, at, width):
        first = int.from_bytes(data[at:at + width], 'little')
        at += width
        high = 1 << (width * 8 - 1)
        if first & high:
            second = int.from_bytes(data[at:at + width], 'little')
            return ((first & (high - 1)) << (8 * width)) | second, at + width
        return first, at

    @staticmethod
    def _encode_length(value, width):
        high = 1 << (width * 8 - 1)
        if value < high:
            return value.to_bytes(width, 'little')
        return ((value >> (8 * width)) | high).to_bytes(width, 'little') + (value & ((high << 1) - 1)).to_bytes(width, 'little')

    def attributes(self):
        for chunk in self.chunks:
            if struct.unpack_from('<H', chunk)[0] != 0x102:
                continue
            _, tag, start, size, count = struct.unpack_from('<IIHHH', chunk, 16)
            for i in range(count):
                at = 16 + start + i * size
                namespace, name, raw, typed_size, reserved, kind, value = struct.unpack_from('<IIIHBBI', chunk, at)
                yield self.strings[tag], self.strings[name], kind, value, raw, chunk, at

    def values(self):
        for tag, name, kind, value, raw, chunk, at in self.attributes():
            yield tag, name, (self.strings[value] if kind == 3 else bool(value) if kind == 18 else value)

    def serialize(self) -> bytes:
        pool = self.pool
        old_count, styles, flags, old_start, old_styles = struct.unpack_from('<5I', pool, 8)
        count = len(self.strings)
        header = struct.unpack_from('<H', pool, 2)[0]
        offsets, body = [], bytearray()
        for value in self.strings:
            offsets.append(len(body))
            utf16_length = len(value.encode('utf-16le')) // 2
            if flags & 256:
                encoded = value.encode('utf-8')
                body += self._encode_length(utf16_length, 1) + self._encode_length(len(encoded), 1) + encoded + b'\0'
            else:
                body += self._encode_length(utf16_length, 2) + value.encode('utf-16le') + b'\0\0'
        body += b'\0' * (-len(body) % 4)
        start = header + 4 * (count + styles)
        style_data = pool[old_styles:] if old_styles else b''
        replacement = bytearray(pool[:header])
        struct.pack_into('<5I', replacement, 8, count, styles, flags & ~1,
                         start, start + len(body) if styles else 0)
        replacement += struct.pack('<' + 'I' * count, *offsets)
        replacement += pool[header + 4 * old_count:header + 4 * (old_count + styles)]
        replacement += body + style_data
        struct.pack_into('<I', replacement, 4, len(replacement))
        chunks = [replacement if c is pool else c for c in self.chunks]
        result = b''.join(chunks)
        return struct.pack('<HHI', 3, 8, len(result) + 8) + result


def patch_manifest(data: bytes) -> bytes:
    xml = AndroidXml(data)
    if OLD_PACKAGE not in xml.strings:
        raise ValueError('Unexpected original Android package')
    xml.strings = [s.replace(OLD_PACKAGE, PACKAGE) for s in xml.strings]
    label_index = len(xml.strings)
    xml.strings.append(APP_LABEL)
    found = 0
    labels = 0
    for tag, name, kind, value, raw, chunk, at in xml.attributes():
        if tag == 'manifest' and name == 'versionName' and kind == 3:
            if xml.strings[value] != '2.32.0':
                raise ValueError("Unexpected APK versionName")
            xml.strings[value] = VERSION
            if raw != 0xffffffff:
                xml.strings[raw] = VERSION
            found += 1
        if tag == 'application' and name == 'label':
            struct.pack_into('<I', chunk, at + 8, label_index)
            struct.pack_into('<HBBI', chunk, at + 12, 8, 0, 3, label_index)
            labels += 1
    if found != 1 or labels != 1:
        raise ValueError("Expected one manifest versionName and application label")
    return xml.serialize()


def patch_resources(data: bytes) -> bytes:
    """Keep the compiled resource package consistent with the manifest."""
    result = bytearray(data)
    kind, header, size = struct.unpack_from('<HHI', data)
    if kind != 2 or size != len(data):
        raise ValueError('Expected Android resource table')
    pos, changed = header, 0
    while pos < len(data):
        kind, header, size = struct.unpack_from('<HHI', data, pos)
        if size < header or pos + size > len(data):
            raise ValueError('Malformed resource table chunk')
        if kind == 0x200:
            package = data[pos + 12:pos + 268].decode('utf-16le').rstrip('\0')
            if package == OLD_PACKAGE:
                result[pos + 12:pos + 268] = PACKAGE.encode('utf-16le').ljust(256, b'\0')
                changed += 1
        pos += size
    if changed != 1:
        raise ValueError('Expected exactly one original resource package')
    return bytes(result)


def patch_network_security(data: bytes, allow_http: bool) -> bytes:
    xml = AndroidXml(data)
    found = 0
    for tag, name, kind, value, raw, chunk, at in xml.attributes():
        if name == 'cleartextTrafficPermitted':
            struct.pack_into('<I', chunk, at + 8, 0xffffffff)
            struct.pack_into('<HBBI', chunk, at + 12, 8, 0, 18, 0xffffffff if allow_http else 0)
            found += 1
    if not found:
        raise ValueError("Network configuration has no explicit cleartext policy")
    return xml.serialize()


def _unity_string(value: str) -> bytes:
    data = value.encode()
    return struct.pack('<i', len(data)) + data + b'\0' * (-len(data) % 4)


def _read_unity_string(data: bytes, offset: int) -> str:
    length = struct.unpack_from('<i', data, offset)[0]
    if length < 0 or offset + length + 4 > len(data):
        raise ValueError("Invalid Unity string")
    return data[offset + 4:offset + 4 + length].decode()


def _unity_objects(env):
    assets = {a.name: a for a in env.assets}
    resources = assets['resources.assets']
    # Path IDs and field offsets are valid only for the fingerprinted APK.
    return resources.objects[150717], resources.objects[150718], next(
        o for o in env.objects if o.type.name == 'PlayerSettings')


def unity_configuration(data: bytes) -> dict:
    import UnityPy
    env = UnityPy.load(data)
    pun, voice, player = _unity_objects(env)
    p, v, settings = pun.get_raw_data(), voice.get_raw_data(), player.get_raw_data()
    return {'version': _read_unity_string(settings, 608),
            'realtime': _read_unity_string(p, 0x34),
            'voice': _read_unity_string(p, 0x64),
            'voice_asset': _read_unity_string(v, 0x3c),
            'regions': [_read_unity_string(p, 0x94), _read_unity_string(v, 0x6c)]}


def patch_unity(data: bytes, realtime: str, voice: str, region: str = '') -> bytes:
    import UnityPy
    if not re.fullmatch('[a-z0-9]*', region):
        raise ValueError("Photon region must be a region code or empty")
    env = UnityPy.load(data)
    pun_object, voice_object, player = _unity_objects(env)
    pun, voice_data = bytearray(pun_object.get_raw_data()), bytearray(voice_object.get_raw_data())
    original_ids = {_read_unity_string(raw, offset).lower() for raw, offset in
                    [(pun, 0x34), (pun, 0x64), (voice_data, 0x3c)]}
    if realtime.lower() in original_ids or voice.lower() in original_ids:
        raise ValueError('Use host-owned Photon IDs, not the source Unity apps')
    for raw, offset in [(pun, 0x34), (pun, 0x64), (voice_data, 0x3c)]:
        if not GUID.fullmatch(_read_unity_string(raw, offset)):
            raise ValueError("Unexpected Unity Photon settings layout")
    pun[0x34:0x5c] = _unity_string(realtime)
    pun[0x64:0x8c] = _unity_string(voice)
    voice_data[0x3c:0x64] = _unity_string(voice)
    for raw, offset in [(pun, 0x94), (voice_data, 0x6c)]:
        old = _unity_string(_read_unity_string(raw, offset))
        raw[offset:offset + len(old)] = _unity_string(region)
    pun_object.set_raw_data(bytes(pun))
    voice_object.set_raw_data(bytes(voice_data))
    settings = player.get_raw_data()
    old_version = _unity_string('2.32.0')
    if settings[608:608 + len(old_version)] != old_version:
        raise ValueError("Unexpected Unity PlayerSettings version layout")
    player.set_raw_data(settings[:608] + _unity_string(VERSION) + settings[608 + len(old_version):])
    return env.file.save(packer='lz4')
