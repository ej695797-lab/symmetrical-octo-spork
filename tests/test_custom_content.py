import io
import os
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'data'))


def text(value):
    b = value.encode()
    return struct.pack('<i', len(b)) + b + bytes(-len(b) % 4)


def patterns_fixture():
    pattern = (text('old') + text('Old name') + text('mapObject') + struct.pack('<i', 0)
               + text('white') + struct.pack('<ii', 75, 0) + text(''))
    dataset = (text('stuffed') + text('Pillows') + struct.pack('<ii', 0, 3) + text('')
               + struct.pack('<5i', 0, 0, 0, 0, 0) + text('')
               + struct.pack('<i', 1) + pattern)
    return bytes(32) + struct.pack('<i', 1) + dataset + b'TAIL'


class CustomContentTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('BUDDY_CUSTOM_DATA'), 'set BUDDY_CUSTOM_DATA for generated-prefab interaction checks')
    def test_generated_fumo_preserves_pillow_pickup_tag_and_collider(self):
        import UnityPy
        from buddy_content import resolve
        env = UnityPy.load(str(Path(os.environ['BUDDY_CUSTOM_DATA'])/'data.unity3d'))
        rm = next(o for o in env.objects if o.type.name == 'ResourceManager')
        refs = dict(rm.read_typetree()['m_Container'])
        pillow = resolve(rm, refs['mapobjects/stuffed/1x1_stuffed']).read_typetree()
        fumo = resolve(rm, refs['mapobjects/complexdecorationstuffed/1x1_stuffedfumo'])
        root = fumo.read_typetree()
        tags = next(o for o in env.objects if o.type.name == 'TagManager').read_typetree()['tags']
        destructable_tag = 20000 + tags.index('Destructable')
        self.assertEqual(root['m_Tag'], destructable_tag,
                         'Fumo collider must be tagged Destructable for block interaction')
        self.assertEqual(root['m_Tag'], pillow['m_Tag'])
        self.assertEqual(root['m_Layer'], pillow['m_Layer'])
        collider = next(resolve(fumo, c['component']) for c in root['m_Component']
                        if resolve(fumo, c['component']).type.name == 'BoxCollider').read_typetree()
        self.assertTrue(collider['m_Enabled'])
        self.assertFalse(collider['m_IsTrigger'])
        self.assertEqual(collider['m_GameObject']['m_PathID'], fumo.path_id)

    def test_modern_pattern_append_preserves_existing_names_and_tail(self):
        import buddy_content as content
        raw = patterns_fixture()
        changed = content.append_pattern(raw, 'stuffed', '1x1_stuffedFumo', 'Reimu Fumo',
                                         'mapObject', [], 'white', 0)
        sets, end = content.parse_patterns(changed)
        self.assertEqual([p['displayName'] for p in sets[0]['patterns']], ['Old name', 'Reimu Fumo'])
        self.assertEqual(changed[end:], b'TAIL')
        self.assertEqual(sets[0]['patterns'][1]['key'], '1x1_stuffedFumo')
        with self.assertRaisesRegex(ValueError, 'already'):
            content.append_pattern(changed, 'stuffed', 'old', 'Duplicate', 'mapObject', [], '', 0)

    def test_truncated_pattern_asset_is_rejected(self):
        import buddy_content as content
        with self.assertRaises(ValueError):
            content.parse_patterns(patterns_fixture()[:65])

    def test_audio_append_allocates_new_id_and_preserves_existing_data(self):
        import buddy_content as content
        entry = (text('song_old') + text('Old title') + text('Artist') + struct.pack('<iqf', 2, 123, .8)
                 + struct.pack('<i', 1) + struct.pack('<ffi', 0, 120, 4)
                 + struct.pack('<if', 1, 2.0) + struct.pack('<f', 0))
        raw = bytes(32) + struct.pack('<i', 1) + entry + b'TAIL'
        changed, audio_id = content.append_audio(raw, 'song_verity', 'Verity', 'Horror Skunx',
                                                  2, 456, 12.5)
        self.assertEqual(audio_id, 1)
        entries, end = content.parse_audio(changed)
        self.assertEqual(changed[36:36+len(entry)], entry)
        self.assertEqual(entries[1]['title'], 'Verity')
        self.assertEqual(entries[1]['clip'], (2, 456))
        self.assertEqual(changed[end:], b'TAIL')

    def test_record_uses_media_id_separate_from_audio_id(self):
        import buddy_content as content
        old = text('record_old') + struct.pack('<iiq', 6, 2, 330)
        raw = bytes(32) + struct.pack('<i', 1) + old + b'TAPES'
        changed, media_id = content.append_media(raw, 'record_verity', 133, 2, 999)
        entries, end = content.parse_media(changed)
        self.assertEqual(media_id, 1)
        self.assertEqual(entries[1]['audio_id'], 133)
        self.assertEqual(entries[1]['art'], (2, 999))
        self.assertEqual(changed[36:36+len(old)], old)
        self.assertEqual(changed[end:], b'TAPES')

    def test_extract_includes_modern_streamed_resources(self):
        import extract_data
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            apk = Path(tmp) / 'source.apk'
            with zipfile.ZipFile(apk, 'w') as z:
                for name, data in [('data.unity3d', b'bundle'), ('sharedassets1.resource', b'audio1'),
                                   ('sharedassets2.resource', b'audio2'), ('resources.resource', b'textures')]:
                    z.writestr('assets/bin/Data/'+name, data)
            out = Path(tmp) / 'data'
            extract_data.main(str(apk), str(out))
            self.assertEqual((out/'sharedassets2.resource').read_bytes(), b'audio2')
            self.assertEqual((out/'resources.resource').read_bytes(), b'textures')

    def test_pcm_bank_has_valid_single_sample_sizes(self):
        from add_music_disc import fsb_pcm
        pcm = struct.pack('<4h', 0, 100, -100, 0)
        bank = fsb_pcm(pcm)
        self.assertEqual(bank[:4], b'FSB5')
        self.assertEqual(struct.unpack_from('<6I', bank, 4), (1, 1, 36, 0, 8, 2))
        self.assertEqual(struct.unpack_from('<Q', bank, 60)[0] >> 34, 4)
        self.assertEqual(bank[96:], pcm)
        for bad in [b'', b'x']:
            with self.assertRaises(ValueError): fsb_pcm(bad)

    def test_scripts_refuse_source_overwrite_before_reading_assets(self):
        from add_model_block import add_model
        from add_music_disc import add_disc
        with self.assertRaisesRegex(ValueError, 'different output'):
            add_model('missing', 'missing', 'missing.glb', 'fumo', 'Fumo')
        with self.assertRaisesRegex(ValueError, 'new directory'):
            add_disc('missing', 'missing', 'missing.mp3', 'record_verity', 'Verity')

    def test_fumo_cutout_material_preserves_transparency(self):
        from add_model_block import configure_material
        original = {'m_ValidKeywords': [], 'm_InvalidKeywords': [], 'm_CustomRenderQueue': -1,
                    'stringTagMap': [('RenderType', 'Opaque')],
                    'm_SavedProperties': {'m_Floats': [('_Cull', 2.), ('_AlphaClip', 0.), ('_Cutoff', .25)]}}
        result = configure_material(original, {'alphaMode': 'MASK', 'alphaCutoff': .05, 'doubleSided': True})
        props = dict(result['m_SavedProperties']['m_Floats'])
        self.assertEqual(props['_AlphaClip'], 1.)
        self.assertEqual(props['_Cutoff'], .05)
        self.assertEqual(props['_Cull'], 0.)
        self.assertIn('_ALPHATEST_ON', result['m_ValidKeywords'])
        self.assertIn(('RenderType', 'TransparentCutout'), result['stringTagMap'])
        self.assertEqual(original['m_ValidKeywords'], [])
        with self.assertRaisesRegex(ValueError, 'BLEND'):
            configure_material(original, {'alphaMode': 'BLEND'})

    def test_glb_applies_hierarchy_normals_uvs_and_winding(self):
        import glb_model
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'triangle.glb'
            pos = struct.pack('<9f', 0, 0, 0, 1, 0, 0, 0, 1, 0)
            normal = struct.pack('<9f', *([0, 0, 1]*3))
            uv = struct.pack('<6f', 0, 0, 1, 0, 0, 1)
            im = io.BytesIO(); Image.new('RGBA', (2, 2), 'red').save(im, format='PNG')
            blob = pos + normal + uv + im.getvalue()
            doc = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
                   'nodes': [{'translation': [5, 4, 3], 'children': [1]},
                             {'scale': [2, 1, 1], 'mesh': 0}],
                   'buffers': [{'byteLength': len(blob)}],
                   'bufferViews': [{'buffer': 0, 'byteOffset': o, 'byteLength': n}
                                   for o,n in [(0,36),(36,36),(72,24),(96,len(im.getvalue()))]],
                   'accessors': [{'bufferView': i, 'componentType': 5126, 'count': 3, 'type': t}
                                 for i,t in enumerate(['VEC3','VEC3','VEC2'])],
                   'meshes': [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 1, 'TEXCOORD_0': 2},
                                              'material': 0}]}],
                   'materials': [{'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}}}],
                   'textures': [{'source': 0, 'sampler': 0}], 'samplers': [{'magFilter': 9728, 'minFilter': 9728, 'wrapS': 33071, 'wrapT': 33071}], 'images': [{'bufferView': 3, 'mimeType': 'image/png'}]}
            j = json.dumps(doc).encode(); j += b' ' * (-len(j)%4)
            blob += bytes(-len(blob)%4)
            path.write_bytes(struct.pack('<III',0x46546c67,2,28+len(j)+len(blob))
                             +struct.pack('<II',len(j),0x4e4f534a)+j
                             +struct.pack('<II',len(blob),0x004e4942)+blob)
            model = glb_model.load_glb(path)
            self.assertEqual(model['positions'], [(-.25,-.125,0.0),(.25,-.125,0.0),(-.25,.125,0.0)])
            self.assertEqual(model['indices'], [0,2,1])
            self.assertEqual(model['normals'], [(0.0,0.0,-1.0)]*3)
            self.assertEqual(model['uv'], [(0.0,1.0),(1.0,1.0),(0.0,0.0)])
            self.assertEqual(model['image'].getpixel((0,0)), (255,0,0,255))
            self.assertEqual(model['sampler']['magFilter'], 9728)
            self.assertEqual(model['sampler']['wrapS'], 33071)
            path.write_bytes(b'bad')
            with self.assertRaises(ValueError): glb_model.load_glb(path)


if __name__ == '__main__':
    unittest.main()
