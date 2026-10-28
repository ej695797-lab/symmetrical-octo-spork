import os
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))


class BuddyApkTests(unittest.TestCase):
    def test_custom_data_overlays_bundle_and_all_streamed_resources(self):
        import tempfile
        from configure_buddy_apk import custom_data_entries
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.apk'
            data = root/'custom'
            data.mkdir()
            entries = {'data.unity3d': b'new-bundle', 'sharedassets1.resource': b'new-one',
                       'sharedassets2.resource': b'new-two', 'resources.resource': b'textures'}
            with zipfile.ZipFile(source, 'w') as z:
                for name in entries: z.writestr('assets/bin/Data/'+name, b'old')
                z.writestr('lib/arm64-v8a/libBNMExplorer.so', b'native')
            for name, body in entries.items(): (data/name).write_bytes(body)
            (data/'custom-disc.json').write_text('{}')
            with zipfile.ZipFile(source) as z:
                overlays = custom_data_entries(z, data)
                self.assertEqual(set(overlays), {'assets/bin/Data/'+n for n in entries})
                self.assertEqual(overlays['assets/bin/Data/sharedassets2.resource'].read_bytes(), b'new-two')
                self.assertNotIn('lib/arm64-v8a/libBNMExplorer.so', overlays)
                (data/'sharedassets2.resource').unlink()
                with self.assertRaisesRegex(ValueError, 'sharedassets2.resource'):
                    custom_data_entries(z, data)

    @unittest.skipUnless(os.environ.get('BUDDY_SOURCE_APK'), 'set BUDDY_SOURCE_APK for actual-APK verification')
    def test_resource_package_is_renamed_without_changing_resource_ids(self):
        from buddy_apk import patch_resources, OLD_PACKAGE, PACKAGE
        with zipfile.ZipFile(os.environ['BUDDY_SOURCE_APK']) as z:
            data = z.read('resources.arsc')
        old = OLD_PACKAGE.encode('utf-16le').ljust(256, b'\0')
        new = PACKAGE.encode('utf-16le').ljust(256, b'\0')
        patched = patch_resources(data)
        self.assertEqual(patched.replace(new, old), data)
        self.assertNotIn(old, patched)

    @unittest.skipUnless(os.environ.get('BUDDY_SOURCE_APK'), 'set BUDDY_SOURCE_APK for actual-APK verification')
    def test_author_photon_ids_are_refused_even_with_different_case(self):
        from buddy_apk import Elf, patch_native
        with zipfile.ZipFile(os.environ['BUDDY_SOURCE_APK']) as z:
            original = z.read('lib/arm64-v8a/libBNMExplorer.so')
        original_id = Elf(original).pointer_string('_ZL15kOurPhotonAppId')
        with self.assertRaisesRegex(ValueError, 'host-owned'):
            patch_native(original, 'https://buddy.example',
                         '11111111-1111-1111-1111-111111111111', original_id.upper())

    @unittest.skipUnless(os.environ.get('BUDDY_SOURCE_APK'), 'set BUDDY_SOURCE_APK for actual-APK verification')
    def test_separate_author_voice_asset_id_is_refused(self):
        import UnityPy
        from buddy_apk import patch_unity, _unity_objects, _read_unity_string
        with zipfile.ZipFile(os.environ['BUDDY_SOURCE_APK']) as z:
            data = z.read('assets/bin/Data/data.unity3d')
        env = UnityPy.load(data)
        _, voice, _ = _unity_objects(env)
        original_id = _read_unity_string(voice.get_raw_data(), 0x3c)
        del env, voice
        with self.assertRaisesRegex(ValueError, 'host-owned'):
            patch_unity(data, original_id.upper(), '22222222-2222-2222-2222-222222222222')

    @unittest.skipUnless(os.environ.get('BUDDY_SOURCE_APK'), 'set BUDDY_SOURCE_APK for actual-APK verification')
    def test_unity_version_and_both_photon_assets_round_trip(self):
        from buddy_apk import patch_unity, unity_configuration
        with zipfile.ZipFile(os.environ['BUDDY_SOURCE_APK']) as z:
            data = z.read('assets/bin/Data/data.unity3d')
        patched = patch_unity(data, '11111111-1111-1111-1111-111111111111',
                              '22222222-2222-2222-2222-222222222222', 'eu')
        settings = unity_configuration(patched)
        self.assertEqual(settings['version'], '2.32.0-buddy')
        self.assertEqual(settings['realtime'], '11111111-1111-1111-1111-111111111111')
        self.assertEqual(settings['voice'], '22222222-2222-2222-2222-222222222222')
        self.assertEqual(settings['voice_asset'], settings['voice'])
        self.assertEqual(settings['regions'], ['eu', 'eu'])

    @unittest.skipUnless(os.environ.get('BUDDY_SOURCE_APK'), 'set BUDDY_SOURCE_APK for actual-APK verification')
    def test_manifest_version_and_network_policy_are_edited(self):
        from buddy_apk import AndroidXml, patch_manifest, patch_network_security
        with zipfile.ZipFile(os.environ['BUDDY_SOURCE_APK']) as z:
            patched = AndroidXml(patch_manifest(z.read('AndroidManifest.xml')))
            versions = [value for tag, name, value in patched.values() if tag == 'manifest' and name == 'versionName']
            self.assertEqual(versions, ['2.32.0-buddy'])
            values = list(patched.values())
            self.assertIn(('manifest', 'package', 'com.BuddyGames.Buddy'), values)
            self.assertIn(('application', 'label', 'Yeeps Buddy'), values)
            policies = []
            for name in z.namelist():
                if name.startswith('res/') and name.endswith('.xml'):
                    xml = AndroidXml(z.read(name))
                    if 'network-security-config' in xml.strings:
                        policies.append(patch_network_security(z.read(name), True))
            self.assertEqual(len(policies), 1)
            values = [value for tag, name, value in AndroidXml(policies[0]).values()
                      if name == 'cleartextTrafficPermitted']
            self.assertTrue(values)
            self.assertTrue(all(values))

    def test_unknown_native_library_is_refused(self):
        from buddy_apk import patch_native
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            patch_native(b'not the inspected ELF', 'http://192.168.1.9:8000',
                         '11111111-1111-1111-1111-111111111111',
                         '22222222-2222-2222-2222-222222222222')

    def test_invalid_server_is_refused(self):
        from buddy_apk import validate_settings
        for server in ['file:///tmp/x', 'https://host/path', 'https://u:p@host',
                       'https://host?x=1', 'https://host\nother=bad',
                       'https://LIVE.YEEPS-TOOLS.ORG.', 'https://host\x00.invalid']:
            with self.subTest(server=server), self.assertRaises(ValueError):
                validate_settings(server, '11111111-1111-1111-1111-111111111111',
                                  '22222222-2222-2222-2222-222222222222')

    @unittest.skipUnless(os.environ.get('BUDDY_SOURCE_APK'), 'set BUDDY_SOURCE_APK for actual-APK verification')
    def test_actual_native_patch_preserves_bss_and_retargets_all_hosts(self):
        from buddy_apk import Elf, patch_native
        with zipfile.ZipFile(os.environ['BUDDY_SOURCE_APK']) as z:
            original = z.read('lib/arm64-v8a/libBNMExplorer.so')
        patched = patch_native(original, 'http://192.168.1.9:8000',
                               '11111111-1111-1111-1111-111111111111',
                               '22222222-2222-2222-2222-222222222222')
        before, after = Elf(original), Elf(patched)
        self.assertNotIn(b'live.yeeps-tools.org', patched)
        self.assertEqual(after.pointer_string('_ZL9kSelfBase'),
                         'http://192.168.1.9:8000/xxxxxxxxxxxxxxxxxx')
        self.assertEqual(after.pointer_string('_ZL15kOurPhotonAppId'),
                         '11111111-1111-1111-1111-111111111111')
        loads = [h for h in after.programs if h[0] == 1]
        self.assertEqual([h[3] for h in loads], sorted(h[3] for h in loads))
        for load in [h for h in before.programs if h[0] == 1]:
            matching = next(h for h in loads if h[3] == load[3])
            self.assertEqual(matching[:6], load[:6])
            self.assertGreaterEqual(matching[6], load[6])
            self.assertEqual(matching[7], load[7])
        self.assertNotIn(b'com.TrassGames.Yeeps', patched)
        for func in ['_ZL19InstallRemoteClientv', '_ZL18DevlogUploadThreadv']:
            off = after.offset(after.symbols[func][0])
            self.assertEqual(patched[off:off + 4], bytes.fromhex('c0035fd6'))


if __name__ == '__main__':
    unittest.main()
