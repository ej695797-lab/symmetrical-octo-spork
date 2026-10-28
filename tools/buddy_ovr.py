"""Port the earlier Yeepster sideload fix to the fingerprinted live79 ELF.

This provides a private-server local identity only for a failed logged-in-user
response. It does not authenticate a Meta identity. Successful SDK responses
continue through their original functions.
"""
import struct

GETTERS = [
    ('ovr_Message_GetUser', 'message'), ('ovr_User_GetID', 'id'),
    ('ovr_User_GetOculusID', 'name'), ('ovr_User_GetDisplayName', 'name'),
    ('ovr_User_GetImageUrl', 'empty'), ('ovr_User_GetSmallImageUrl', 'empty'),
    ('ovr_User_GetPresence', 'empty'), ('ovr_User_GetPresenceDeeplinkMessage', 'empty'),
    ('ovr_User_GetPresenceDestinationApiName', 'empty'),
    ('ovr_User_GetPresenceLobbySessionId', 'empty'),
    ('ovr_User_GetPresenceMatchSessionId', 'empty'), ('ovr_User_GetPresenceStatus', 'online'),
    ('ovr_User_GetManagedInfo', 'null'),
]
LOGGED_IN_USER = 1131361373
SIDELOAD_MESSAGES = (0x186B58B1, 578880643, 567009472, 1484532365, 645723971, LOGGED_IN_USER)
# Publish the sentinel-producing message hook after all of its field getters.
HOOK_ORDER = list(range(1, len(GETTERS))) + [0]


def profile_locations(elf) -> dict:
    loads = [(i, h) for i, h in enumerate(elf.programs) if h[0] == 1 and h[1] & 2]
    index, last = max(loads, key=lambda item: item[1][3])
    slots = (last[3] + last[6] + 15) & ~15
    sdk_slots = slots + (len(GETTERS) + 1) * 8
    memory_end = sdk_slots + len(GETTERS) * 8
    base = (memory_end + 0x3fff) & ~0x3fff
    return {'base': base, 'slots': slots, 'flag': slots + len(GETTERS) * 8, 'sdk_slots': sdk_slots,
            'keeper': base + ((2048 + len(GETTERS) * 192 + 255) & ~255),
            'memory_end': memory_end, 'writable_index': index,
            'getters': [base + 2048 + i * 192 for i in range(len(GETTERS))]}


def _imports(elf) -> dict:
    plt, rel = elf.section('.plt'), elf.section('.rela.plt')
    symbols = elf.sections[rel[6]]
    strings = elf.sections[symbols[6]]
    pool = elf.data[strings[4]:strings[4] + strings[5]]
    out = {}
    for i, off in enumerate(range(rel[4], rel[4] + rel[5], rel[9])):
        _, info, _ = struct.unpack_from('<QQq', elf.data, off)
        name = struct.unpack_from('<I', elf.data, symbols[4] + (info >> 32) * symbols[9])[0]
        name = pool[name:pool.index(b'\0', name)].decode()
        out[name] = plt[3] + 32 + i * 16
    return out


def build_profile(elf, image: bytearray, locations: dict) -> bytes:
    from keystone import Ks, KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN
    assembler = Ks(KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN)
    base, slots = locations['base'], locations['slots']
    payload = bytearray(16384)
    next_string, texts = 8192, {}

    def text(value):
        nonlocal next_string
        if value not in texts:
            raw = value.encode() + b'\0'
            if next_string + len(raw) > len(payload):
                raise ValueError('Profile string allocation exceeded')
            payload[next_string:next_string + len(raw)] = raw
            texts[value] = base + next_string
            next_string += len(raw)
        return texts[value]

    def address(register, target):
        return [f'adrp {register}, {target & ~4095:#x}', f'add {register}, {register}, #{target & 4095}']

    def pointer(register, target):
        return [f'adrp {register}, {target & ~4095:#x}', f'ldr {register}, [{register}, #{target & 4095}]']

    def code(at, instructions, size):
        raw = bytes(assembler.asm('\n'.join(instructions), addr=at)[0])
        if len(raw) > size:
            raise ValueError(f'ARM64 allocation exceeded at {at:#x}')
        return raw + bytes.fromhex('1f2003d5') * ((size - len(raw)) // 4)

    def emit(at, instructions, size):
        payload[at - base:at - base + size] = code(at, instructions, size)

    def compare_message(kind):
        return [f'movz w9, #{kind & 65535}', f'movk w9, #{kind >> 16}, lsl #16', 'cmp w0, w9']

    # Extend the existing filter only for known sideload-related message types.
    filter_at, filter_size = elf.symbols['_ZL24hook_ovr_Message_IsErrorPv']
    instructions = ['hint #34', 'stp x29, x30, [sp, #-32]!', 'stp x19, x20, [sp, #16]',
                    'mov x29, sp', 'mov x19, x0']
    instructions += pointer('x8', elf.symbols['_ZL12real_IsError'][0])
    instructions += ['cbz x8, success', 'blr x8', 'and w20, w0, #255', 'cbz w20, success']
    instructions += pointer('x8', elf.symbols['_ZL12real_GetType'][0])
    instructions += ['cbz x8, error', 'mov x0, x19', 'blr x8']
    for kind in SIDELOAD_MESSAGES:
        instructions += compare_message(kind) + ['b.eq success']
    instructions += ['error:', 'mov w0, w20', 'b finish', 'success:', 'mov w0, wzr',
                     'finish:', 'ldp x19, x20, [sp, #16]', 'ldp x29, x30, [sp], #32', 'ret']
    off = elf.offset(filter_at)
    image[off:off + filter_size] = code(filter_at, instructions, filter_size)

    imports = _imports(elf)
    installer = ['hint #34', 'stp x29, x30, [sp, #-48]!', 'stp x19, x20, [sp, #16]',
                 'str x21, [sp, #32]', 'mov x29, sp']
    installer += pointer('x8', locations['flag']) + ['cbnz x8, finish']
    installer += address('x0', text('libovrplatformloader.so'))
    installer += ['mov w1, #6', f'bl {imports["dlopen"]:#x}', 'cbz x0, finish', 'mov x19, x0', 'mov w20, wzr']
    installer += address('x8', locations['flag']) + ['mov x9, #1', 'str x9, [x8]']
    for i in HOOK_ORDER:
        name, kind = GETTERS[i]
        replacement = locations['getters'][i]
        installer += ['mov x0, x19'] + address('x1', text(name))
        installer += [f'bl {imports["dlsym"]:#x}', f'cbz x0, skip{i}']
        # The actual SDK exports are adjacent 12-byte dispatch stubs. A
        # 16-byte inline jump corrupts the following export (Quest SIGILL).
        installer += [f'bl {elf.symbols["_ZL11OvrStubSlotPv"][0]:#x}', f'cbz x0, skip{i}', 'mov x21, x0']
        installer += address('x8', locations['sdk_slots'] + i * 8) + ['str x21, [x8]', 'ldar x9, [x21]', f'cbz x9, skip{i}']
        installer += address('x8', slots + i * 8) + ['str x9, [x8]']
        installer += address('x8', replacement) + ['stlr x8, [x21]', 'add w20, w20, #1', f'skip{i}:']
    installer += address('x8', locations['flag']) + ['mov x9, #2', 'stlr x9, [x8]']
    installer += address('x0', text(f'yeeper: local profile slot hooks installed %d/{len(GETTERS)}'))
    installer += ['mov w1, w20', f'bl {elf.symbols["_Z19BNM_FileCheckpointFPKcz"][0]:#x}',
                  'finish:', 'ldr x21, [sp, #32]', 'ldp x19, x20, [sp, #16]', 'ldp x29, x30, [sp], #48', 'ret']
    emit(base, installer, 2048)

    for i, ((name, kind), target) in enumerate(zip(GETTERS, locations['getters'])):
        original = pointer('x8', slots + i * 8)
        if kind == 'message':
            instructions = ['hint #34', 'stp x29, x30, [sp, #-32]!', 'str x19, [sp, #16]',
                            'mov x29, sp', 'mov x19, x0']
            instructions += pointer('x8', elf.symbols['_ZL12real_IsError'][0])
            instructions += ['cbz x8, real', 'blr x8', 'cbz w0, real']
            instructions += pointer('x8', elf.symbols['_ZL12real_GetType'][0])
            instructions += ['cbz x8, real', 'mov x0, x19', 'blr x8']
            instructions += compare_message(LOGGED_IN_USER) + ['b.ne real', 'mov x0, #1', 'b finish', 'real:', 'mov x0, x19']
            instructions += original + ['blr x8', 'finish:', 'ldr x19, [sp, #16]', 'ldp x29, x30, [sp], #32', 'ret']
            emit(target, instructions, 192)
            continue
        instructions = ['hint #34', 'cmp x0, #1', 'b.ne real']
        if kind == 'id':
            instructions += address('x8', elf.symbols['_ZL11g_deviceKey'][0])
            instructions += ['ldrb w9, [x8]', 'tbz w9, #0, small', 'ldr x8, [x8, #16]',
                'b ready', 'small:', 'add x8, x8, #1', 'ready:', 'cbz x8, missing',
                'ldrb w9, [x8]', 'cbz w9, missing', 'movz x0, #0x2325',
                'movk x0, #0x8422, lsl #16', 'movk x0, #0x9ce4, lsl #32',
                'movk x0, #0xcbf2, lsl #48', 'movz x10, #0x1b3',
                'movk x10, #0x100, lsl #32', 'loop:', 'ldrb w9, [x8], #1',
                'cbz w9, hashed', 'eor x0, x0, x9', 'mul x0, x0, x10', 'b loop',
                'hashed:', 'and x0, x0, #0x1fffffffffffffff',
                'orr x0, x0, #0x4000000000000000', 'ret', 'missing:', 'mov x0, #0', 'ret']
        elif kind == 'online':
            instructions += ['mov x0, #1', 'ret']
        elif kind == 'null':
            instructions += ['mov x0, xzr', 'ret']
        else:
            instructions += address('x0', text('Yeeper920s Modded Yeeps!' if kind == 'name' else '')) + ['ret']
        instructions += ['real:'] + original + ['br x8']
        emit(target, instructions, 192)

    # SDK initialization replaces dispatch slots with its real implementation.
    # Reuse the existing keeper's 20ms tick, with fixed private records rather
    # than mutating its vector concurrently. Preserve the original sleep args
    # and return address by tail-calling the original sleep function.
    keeper_at, keeper_size = elf.symbols['_ZL13OvrSlotKeeperv']
    keeper_off = elf.offset(keeper_at)
    sleep_targets = {v for name, (v, _) in elf.symbols.items() if 'this_thread9sleep_for' in name}
    sleep_calls = []
    for i in range(0, keeper_size, 4):
        word = struct.unpack_from('<I', elf.data, keeper_off + i)[0]
        if word & 0xfc000000 == 0x94000000:
            immediate = word & 0x3ffffff
            if immediate & 0x2000000:
                immediate -= 0x4000000
            destination = keeper_at + i + immediate * 4
            if destination in sleep_targets:
                sleep_calls.append((i, destination))
    if len(sleep_calls) != 1:
        raise ValueError('Unexpected OVR slot keeper sleep layout')
    call_offset, sleep_target = sleep_calls[0]
    poll = ['hint #34'] + address('x8', locations['flag'])
    poll += ['ldar x8, [x8]', 'cmp x8, #2', 'b.ne sleep']
    for i in HOOK_ORDER:
        poll += pointer('x8', locations['sdk_slots'] + i * 8)
        poll += [f'cbz x8, next{i}', 'ldar x9, [x8]', f'cbz x9, next{i}']
        poll += address('x10', locations['getters'][i])
        poll += ['cmp x9, x10', f'b.eq next{i}']
        poll += address('x11', slots + i * 8)
        poll += ['str x9, [x11]', 'stlr x10, [x8]', f'next{i}:']
    poll += ['sleep:', f'b {sleep_target:#x}']
    emit(locations['keeper'], poll, 1024)
    image[keeper_off + call_offset:keeper_off + call_offset + 4] = code(
        keeper_at + call_offset, [f'bl {locations["keeper"]:#x}'], 4)

    # Tail-call after the existing installer has restored its caller's frame.
    start, size = elf.symbols['_ZL23InstallOvrPlatformHooksPv']
    off = elf.offset(start)
    returns = [i for i in range(0, size, 4) if elf.data[off + i:off + i + 4] == bytes.fromhex('c0035fd6')]
    if len(returns) != 1:
        raise ValueError('Unexpected OVR installer return layout')
    at = start + returns[0]
    image[off + returns[0]:off + returns[0] + 4] = code(at, [f'b {base:#x}'], 4)
    return bytes(payload)
