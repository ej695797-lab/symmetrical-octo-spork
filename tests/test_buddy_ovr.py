"""Execute generated native fallback handlers, not a Python model of them."""
import os
from pathlib import Path
import struct
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))


@unittest.skipUnless(os.environ.get('BUDDY_SOURCE_APK'), 'set BUDDY_SOURCE_APK for ARM64 checks')
class BuddyOvrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from buddy_apk import Elf, patch_native
        from buddy_ovr import profile_locations
        with zipfile.ZipFile(os.environ['BUDDY_SOURCE_APK']) as z:
            source = z.read('lib/arm64-v8a/libBNMExplorer.so')
            cls.sdk = Elf(z.read('lib/arm64-v8a/libovrplatformloader.so'))
        cls.original = Elf(source)
        cls.layout = profile_locations(cls.original)
        cls.image = patch_native(source, 'https://buddy.example',
                                 '11111111-1111-1111-1111-111111111111',
                                 '22222222-2222-2222-2222-222222222222')
        cls.elf = Elf(cls.image)

    def setUp(self):
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
        from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_PC, UC_ARM64_REG_LR
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.base, self.stack, self.external, self.done, self.key_memory = (
            0x10000000, 0x20000000, 0x30000000, 0x40000000, 0x50000000)
        end = max(h[3] + h[6] for h in self.elf.programs if h[0] == 1)
        self.uc.mem_map(self.base, (end + 4095) & ~4095)
        for h in self.elf.programs:
            if h[0] == 1:
                self.uc.mem_write(self.base + h[3], self.image[h[2]:h[2] + h[5]])
        for addr, size in [(self.stack, 65536), (self.external, 4096), (self.done, 4096), (self.key_memory, 4096)]:
            self.uc.mem_map(addr, size)
        self.state = {'error': 1, 'kind': 1131361373}
        self.uc.mem_write(self.base + self.original.symbols['_ZL12real_IsError'][0], struct.pack('<Q', self.external))
        self.uc.mem_write(self.base + self.original.symbols['_ZL12real_GetType'][0], struct.pack('<Q', self.external + 4))

        def external(cpu, pc, size, userdata):
            if pc == self.external or pc == self.external + 4:
                cpu.reg_write(UC_ARM64_REG_X0, self.state['error'] if pc == self.external else self.state['kind'])
                cpu.reg_write(UC_ARM64_REG_PC, cpu.reg_read(UC_ARM64_REG_LR))
            elif self.external + 0x200 <= pc < self.external + 0x240:
                self.assertEqual(cpu.reg_read(UC_ARM64_REG_X0), 0x7777)
                cpu.reg_write(UC_ARM64_REG_X0, 0xABCD)
                cpu.reg_write(UC_ARM64_REG_PC, cpu.reg_read(UC_ARM64_REG_LR))
            elif self.external + 0x300 <= pc < self.external + 0x340:
                self.assertEqual(cpu.reg_read(UC_ARM64_REG_X0), 0x7777)
                cpu.reg_write(UC_ARM64_REG_X0, 0xEF01)
                cpu.reg_write(UC_ARM64_REG_PC, cpu.reg_read(UC_ARM64_REG_LR))

        self.uc.hook_add(UC_HOOK_CODE, external)

    def sdk_exports(self):
        dynamic = self.sdk.section('.dynsym')
        strings = self.sdk.sections[dynamic[6]]
        pool = self.sdk.data[strings[4]:strings[4] + strings[5]]
        exports = {}
        for at in range(dynamic[4], dynamic[4] + dynamic[5], dynamic[9]):
            name, _, _, defined, value, _ = struct.unpack_from('<IBBHQQ', self.sdk.data, at)
            if defined:
                exports[pool[name:pool.index(b'\0', name)].decode()] = value
        return exports

    def call(self, address, arg=0x7777):
        from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_PC, UC_ARM64_REG_SP,
                                         UC_ARM64_REG_LR, UC_ARM64_REG_X19, UC_ARM64_REG_X20,
                                         UC_ARM64_REG_X21)
        self.uc.reg_write(UC_ARM64_REG_SP, self.stack + 65520)
        self.uc.reg_write(UC_ARM64_REG_LR, self.done)
        self.uc.reg_write(UC_ARM64_REG_X19, 0x19191919)
        self.uc.reg_write(UC_ARM64_REG_X20, 0x20202020)
        self.uc.reg_write(UC_ARM64_REG_X21, 0x21212121)
        self.uc.reg_write(UC_ARM64_REG_X0, arg)
        self.uc.emu_start(self.base + address, self.done, count=10000)
        self.assertEqual(self.uc.reg_read(UC_ARM64_REG_PC), self.done)
        self.assertEqual(self.uc.reg_read(UC_ARM64_REG_SP), self.stack + 65520)
        self.assertEqual(self.uc.reg_read(UC_ARM64_REG_X19), 0x19191919)
        self.assertEqual(self.uc.reg_read(UC_ARM64_REG_X20), 0x20202020)
        self.assertEqual(self.uc.reg_read(UC_ARM64_REG_X21), 0x21212121)
        return self.uc.reg_read(UC_ARM64_REG_X0)

    def test_error_filter_only_handles_known_sideload_failures(self):
        target = self.original.symbols['_ZL24hook_ovr_Message_IsErrorPv'][0]
        for kind in [0x186B58B1, 578880643, 567009472, 1484532365, 645723971, 1131361373, 123]:
            for error in [0, 1]:
                self.state.update(kind=kind, error=error)
                self.assertEqual(self.call(target), 1 if kind == 123 and error else 0)

    def test_installer_registers_all_profile_hooks_once(self):
        from buddy_ovr import GETTERS, _imports
        from unicorn import UC_HOOK_CODE
        from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_PC, UC_ARM64_REG_LR
        imports = _imports(self.original)
        sleep_target = next(value for name, (value, _) in self.original.symbols.items()
                            if 'this_thread9sleep_for' in name and 'ELl1000E' in name)
        calls = []
        sdk_base = 0x60000000
        end = max(h[3] + h[6] for h in self.sdk.programs if h[0] == 1)
        self.uc.mem_map(sdk_base, (end + 4095) & ~4095)
        for h in self.sdk.programs:
            if h[0] == 1:
                self.uc.mem_write(sdk_base + h[3], self.sdk.data[h[2]:h[2] + h[5]])
        exports = self.sdk_exports()
        stubs, dispatch_slots = {}, {}
        for i, (name, _) in enumerate(GETTERS):
            address = sdk_base + exports[name]
            stubs[name] = bytes(self.uc.mem_read(address, 12))
            slot = self.call(self.original.symbols['_ZL11OvrStubSlotPv'][0], address)
            self.assertGreaterEqual(slot, sdk_base)
            dispatch_slots[name] = slot
            self.uc.mem_write(slot, struct.pack('<Q', self.external + 0x200 + i * 4))

        def string(at):
            data = bytearray()
            while True:
                byte = self.uc.mem_read(at + len(data), 1)[0]
                if not byte:
                    return data.decode()
                data.append(byte)

        def intercept(cpu, pc, size, userdata):
            result = None
            if pc == self.base + imports['dlopen']:
                self.assertEqual(string(cpu.reg_read(UC_ARM64_REG_X0)), 'libovrplatformloader.so')
                result = 0x1111
            elif pc == self.base + imports['dlsym']:
                name = string(cpu.reg_read(UC_ARM64_REG_X1))
                calls.append(name)
                result = sdk_base + exports[name]
            elif pc == self.base + self.original.symbols['DobbyHook'][0]:
                self.fail('Inline DobbyHook would overwrite adjacent 12-byte SDK stubs')
            elif pc == self.base + self.original.symbols['_Z19BNM_FileCheckpointFPKcz'][0]:
                self.assertEqual(cpu.reg_read(UC_ARM64_REG_X1), len(GETTERS))
                result = 0
            elif pc == self.base + sleep_target:
                self.assertEqual(cpu.reg_read(UC_ARM64_REG_X0), 0x7777)
                result = 0
            if result is not None:
                cpu.reg_write(UC_ARM64_REG_X0, result)
                cpu.reg_write(UC_ARM64_REG_PC, cpu.reg_read(UC_ARM64_REG_LR))

        self.uc.hook_add(UC_HOOK_CODE, intercept)
        self.call(self.layout['base'])
        self.call(self.layout['base'])
        self.assertEqual(calls, [name for name, _ in GETTERS[1:]] + [GETTERS[0][0]])
        for i, (name, _) in enumerate(GETTERS):
            self.assertEqual(bytes(self.uc.mem_read(sdk_base + exports[name], 12)), stubs[name])
            self.assertEqual(struct.unpack('<Q', self.uc.mem_read(dispatch_slots[name], 8))[0],
                             self.base + self.layout['getters'][i])
            self.assertEqual(struct.unpack('<Q', self.uc.mem_read(self.base + self.layout['slots'] + i * 8, 8))[0],
                             self.external + 0x200 + i * 4)
        # Execute the real SDK stub with the local sentinel: this was the
        # SIGILL site on the Quest, not a mocked Dobby registration call.
        self.assertEqual(self.call(sdk_base + exports['ovr_User_GetID'] - self.base, 1), 0)
        self.assertEqual(self.call(sdk_base + exports['ovr_User_GetManagedInfo'] - self.base, 1), 0)
        # The SDK swaps implementations after init. The keeper must capture
        # each new original and restore the hooks, without rewriting stubs.
        for i, (name, _) in enumerate(GETTERS):
            self.uc.mem_write(dispatch_slots[name], struct.pack('<Q', self.external + 0x300 + i * 4))
        self.call(self.layout['keeper'])
        for i, (name, _) in enumerate(GETTERS):
            self.assertEqual(bytes(self.uc.mem_read(sdk_base + exports[name], 12)), stubs[name])
            self.assertEqual(struct.unpack('<Q', self.uc.mem_read(dispatch_slots[name], 8))[0],
                             self.base + self.layout['getters'][i])
            self.assertEqual(struct.unpack('<Q', self.uc.mem_read(self.base + self.layout['slots'] + i * 8, 8))[0],
                             self.external + 0x300 + i * 4)
        self.assertEqual(self.call(sdk_base + exports['ovr_User_GetID'] - self.base), 0xEF01)

    def test_source_sdk_exports_every_hook_target(self):
        from buddy_apk import Elf
        from buddy_ovr import GETTERS
        with zipfile.ZipFile(os.environ['BUDDY_SOURCE_APK']) as z:
            sdk = Elf(z.read('lib/arm64-v8a/libovrplatformloader.so'))
        dynamic = sdk.section('.dynsym')
        strings = sdk.sections[dynamic[6]]
        pool = sdk.data[strings[4]:strings[4] + strings[5]]
        exports = set()
        for at in range(dynamic[4], dynamic[4] + dynamic[5], dynamic[9]):
            name, _, _, defined, _, _ = struct.unpack_from('<IBBHQQ', sdk.data, at)
            if defined:
                exports.add(pool[name:pool.index(b'\0', name)].decode())
        self.assertTrue({name for name, _ in GETTERS}.issubset(exports))

    def test_local_profile_has_no_managed_info_object(self):
        from buddy_ovr import GETTERS
        names = [name for name, _ in GETTERS]
        # live79's User constructor calls this between ImageUrl and OculusID.
        # Forwarding the synthetic user (1) to the real SDK dereferences it.
        self.assertIn('ovr_User_GetManagedInfo', names)
        i = names.index('ovr_User_GetManagedInfo')
        self.uc.mem_write(self.base + self.layout['slots'] + i * 8,
                          struct.pack('<Q', self.external + 0x200 + i * 4))
        self.assertEqual(self.call(self.layout['getters'][i], 1), 0)
        self.assertEqual(self.call(self.layout['getters'][i]), 0xABCD)

    def test_profile_is_stable_per_device_and_preserves_successful_sdk_users(self):
        from buddy_ovr import GETTERS
        for i, (name, kind) in enumerate(GETTERS):
            self.uc.mem_write(self.base + self.layout['slots'] + i * 8,
                              struct.pack('<Q', self.external + 0x200 + i * 4))
        message = self.layout['getters'][0]
        self.assertEqual(self.call(message), 1)
        self.state['error'] = 0
        self.assertEqual(self.call(message), 0xABCD)
        self.state.update(error=1, kind=123)
        self.assertEqual(self.call(message), 0xABCD)
        ids = []
        for key in ['a' * 64, 'b' * 64, 'a' * 64, '', 'short']:
            raw = key.encode() + b'\0'
            if key == 'short':
                value = bytes([len(key) << 1]) + raw
                self.uc.mem_write(self.base + self.original.symbols['_ZL11g_deviceKey'][0], value.ljust(24, b'\0'))
            else:
                self.uc.mem_write(self.key_memory, raw)
                self.uc.mem_write(self.base + self.original.symbols['_ZL11g_deviceKey'][0],
                                  struct.pack('<QQQ', 129, len(key), self.key_memory))
            result = self.call(self.layout['getters'][1], 1)
            expected = 0xCBF29CE484222325
            for byte in key.encode():
                expected = ((expected ^ byte) * 0x100000001B3) & ((1 << 64) - 1)
            self.assertEqual(result, ((expected & 0x1FFFFFFFFFFFFFFF) | 0x4000000000000000) if key else 0)
            ids.append(result)
        self.assertEqual(ids[0], ids[2])
        self.assertNotEqual(ids[0], ids[1])
        for i in range(1, len(GETTERS)):
            self.assertEqual(self.call(self.layout['getters'][i]), 0xABCD)


if __name__ == '__main__':
    unittest.main()
