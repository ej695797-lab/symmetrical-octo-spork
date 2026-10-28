"""Playable public-layout contracts, independent of a connected headset."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from mongomock_motor import AsyncMongoMockClient
from services.compact_world import build_compact_world, install_compact_world
from services.mapsync_decoder import decode_map


class CompactWorldTests(unittest.IsolatedAsyncioTestCase):
    def test_wilds_keeps_original_nonofficial_creative_classification(self):
        # The compact installer must not promote the original creative Wilds
        # into the client's separately handled official-room category.
        world = build_compact_world(12345)
        node = next(n for n in world['graph']['data'] if n.get('roomKey') == 'wild_0')
        self.assertEqual(node['isOfficial'], 0)
        self.assertEqual(world['rooms']['wild_0']['isOfficial'], 0)
        for key in ('nexus', 'official_0', 'official_1'):
            self.assertEqual(world['rooms'][key]['isOfficial'], 1)

    def test_all_destinations_have_reciprocal_open_routes(self):
        world = build_compact_world(12345)
        nodes = world['graph']['data']
        self.assertEqual({n.get('roomKey') for n in nodes if n.get('roomKey')},
                         {'nexus', 'official_0', 'official_1', 'wild_0'})
        positions = {tuple(n['gridPosition']): n for n in nodes}
        self.assertEqual(len(positions), len(nodes))
        self.assertEqual(positions[(0, 0)]['roomKey'], 'nexus')
        self.assertEqual(positions[(1, 0)]['roomKey'], 'official_0')
        self.assertEqual(positions[(2, 0)]['roomKey'], 'wild_0')
        self.assertEqual(positions[(-1, 0)]['transitionKey'], 'private')
        reached, pending = set(), [(0, 0)]
        while pending:
            position = pending.pop()
            if position in reached:
                continue
            reached.add(position)
            node = positions[position]
            for side, dx, dy, opposite in [('Up', 0, 1, 'Down'), ('Right', 1, 0, 'Left'),
                                           ('Down', 0, -1, 'Up'), ('Left', -1, 0, 'Right')]:
                target = (position[0]+dx, position[1]+dy)
                if not node['block'+side]:
                    self.assertIn(target, positions, 'No door may lead off the map')
                    self.assertFalse(positions[target]['block'+opposite])
                    pending.append(target)
                if node.get('roomKey'):
                    room = world['rooms'][node['roomKey']]
                    self.assertEqual(room['door_'+side.lower()]['block'], node['block'+side])
                    self.assertEqual(room['door_'+side.lower()]['position'], [0, 0])
        self.assertEqual(reached, set(positions))

    def test_rooms_load_actual_bounded_builds_and_keep_building_mode(self):
        world = build_compact_world(12345)
        catalog = json.loads((Path(__file__).resolve().parents[1] /
                              'docs/apk-inspection/live79/mapobject-catalog.json').read_text())
        keys = {item['key'] for item in catalog}
        for key, room in world['rooms'].items():
            self.assertEqual(room['dimensions'], [64, 80, 64])
            # Public creative rooms require noSave; otherwise native OnJoinedRoom
            # rejects ordinary players before creative state initializes.
            self.assertEqual(room['noSave'], int(key == 'wild_0'))
            self.assertEqual(room['isNoFloor'], 0)
            self.assertEqual(room['floorDepth'], 0)
            self.assertEqual(room['isCreative'], int(key == 'wild_0'))
            self.assertEqual(room['isModeLocked'], int(key != 'wild_0'))
            blocks = decode_map(world['imports'][key]['saveData'])
            if key == 'wild_0':
                self.assertEqual(blocks, [], 'Wilds must start empty for free building')
            else:
                self.assertGreater(len(blocks), 50)
            self.assertTrue(all(b['name'] in keys for b in blocks))
            self.assertTrue(all(0 <= b[axis] < size for b in blocks
                                for axis, size in zip(('x', 'y', 'z'), room['dimensions'])))
        hub = decode_map(world['imports']['nexus']['saveData'])
        self.assertIn('navigationSignpostBlock', {b['name'] for b in hub})
        self.assertIn('privateWorldsPortal', {b['name'] for b in hub})

    async def test_install_backs_up_public_changes_and_preserves_private_data(self):
        db = AsyncMongoMockClient()['Yeeps232Buddy']
        await db.rooms.insert_many([
            {'roomKey': 'official_0', 'roomName': 'Old Playground', 'customField': 'keep'},
            {'roomKey': 'p_o_owner_sandbox', 'roomName': 'My build'}])
        await db.maps.insert_one({'mapKey': 'p_o_owner_sandbox$current', 'saveData': 'private-bytes'})
        await db.room_maps.insert_one({'versionKey': 'beta4', 'data': [{'old': True}], 'version': 2})
        private_before = await db.maps.find_one({}, {'_id': 0})
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'before.json'
            report = await install_compact_world(db, backup, 12345)
            saved = json.loads(backup.read_text())
            self.assertTrue(any(d['roomName'] == 'Old Playground' for d in saved['rooms']))
            self.assertEqual(report['privateBefore'], report['privateAfter'])
            hub = await db.rooms.find_one({'roomKey': 'nexus'})
            self.assertEqual(hub['noSave'], 0)
            self.assertEqual((await db.rooms.find_one({'roomKey': 'official_0'}))['customField'], 'keep')
            graph = await db.room_maps.find_one({'roomMapKey': 'trickOrTreat_0'})
            self.assertEqual(len(graph['graph']['data']), 5)
            self.assertEqual(len(graph['data']), 5, 'Dynamo and Lambda see the same layout')
            self.assertEqual((await db.room_maps.find_one({'versionKey': 'beta4'}))['version'], 2)
            self.assertEqual(await db.maps.find_one({}, {'_id': 0}), private_before)

    async def test_reinstall_preserves_edits_and_never_overwrites_backup(self):
        db = AsyncMongoMockClient()['Yeeps232Buddy']
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'before.json'
            await install_compact_world(db, backup, 12345)
            original = backup.read_bytes()
            await db.rooms.update_one({'roomKey': 'nexus'}, {'$set': {'roomName': 'Custom Hub'}})
            await db.imported_maps.update_one({'roomKey': 'nexus'}, {'$set': {'saveData': 'custom-map'}})
            result = await install_compact_world(db, backup, 12346)
            self.assertFalse(result['changed'])
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual((await db.rooms.find_one({'roomKey': 'nexus'}))['roomName'], 'Custom Hub')
            self.assertEqual((await db.imported_maps.find_one({'roomKey': 'nexus'}))['saveData'], 'custom-map')

    async def test_refuses_other_databases_before_writing(self):
        db = AsyncMongoMockClient()['Yeeps131']
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'before.json'
            with self.assertRaises(ValueError):
                await install_compact_world(db, backup, 12345)
            self.assertFalse(backup.exists())
            self.assertEqual(await db.rooms.count_documents({}), 0)

    async def test_install_recovers_when_only_one_graph_alias_was_published(self):
        from services.compact_world import LAYOUT_ID
        db = AsyncMongoMockClient()['Yeeps232Buddy']
        await db.room_maps.insert_one({'roomMapKey': 'trickOrTreat_0', '_buddyLayout': LAYOUT_ID})
        with tempfile.TemporaryDirectory() as directory:
            result = await install_compact_world(db, Path(directory) / 'partial.json', 12345)
            self.assertTrue(result['changed'])
            public = await db.room_maps.find_one({'roomMapKey': 'public'})
            self.assertIsNotNone(public)
            self.assertEqual(len(public['graph']['data']), 5)

    async def test_old_public_terrain_cannot_obstruct_new_builds(self):
        from handlers import maps
        import services.db as storage
        db = AsyncMongoMockClient()['Yeeps232Buddy']
        await db.maps.insert_many([
            {'mapKey': 'official_0$current', 'saveData': 'old-public-build',
             'terrainSaveData': 'old-terrain', 'terrainDataVersion': 7},
            {'mapKey': 'p_o_owner$current', 'terrainSaveData': 'private-terrain'}])
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'before.json'
            await install_compact_world(db, backup, 12345)
            self.assertIn('old-terrain', backup.read_text())
            with patch.object(maps, 'maps', db.maps), patch.object(maps, 'IS_BUDDY', True), \
                    patch.object(storage, 'imported_maps', db.imported_maps):
                terrain = await maps.handle_fetch_map_data({'roomKey': 'official_0', 'isTerrain': 1})
                self.assertEqual(terrain, {'saveData': '', 'mapDataVersion': 1})
                build = await maps.handle_fetch_map_data({'roomKey': 'official_0',
                    'isTerrain': 0, 'targetSessionID': 12345, 'saveOffset': 0})
                self.assertGreater(len(decode_map(build['saveData'])), 50)
            self.assertEqual((await db.maps.find_one({'mapKey': 'official_0$current'}))['saveData'],
                             'old-public-build')
            self.assertEqual((await db.maps.find_one({'mapKey': 'p_o_owner$current'}))['terrainSaveData'],
                             'private-terrain')
