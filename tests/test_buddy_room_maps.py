"""Room graph compatibility uses an isolated store and never migrates host data."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))

from mongomock_motor import AsyncMongoMockClient
from handlers import rooms


class BuddyRoomMapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.collection = AsyncMongoMockClient()['room_map_tests']['room_maps']
        self.rooms = AsyncMongoMockClient()['room_map_tests']['rooms']
        self.patches = [patch.object(rooms, 'room_maps', self.collection),
                        patch.object(rooms, 'rooms', self.rooms),
                        patch('config.IS_BUDDY', True)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.seed = json.loads((Path(__file__).resolve().parents[1] /
            'server/seed/world/room_maps.json').read_text())[0]
        await self.collection.insert_one(dict(self.seed))

    async def test_starting_map_contains_seeded_official_room(self):
        result = await rooms.handle_room_map_data({'roomMapKey': 'trickOrTreat_0'})
        graph = result['data']
        self.assertEqual(graph['uniqueID'], 'trickOrTreat_0')
        self.assertEqual(graph['version'], self.seed['version'] + 1)
        self.assertEqual(graph['data'][:-1], self.seed['data'])
        self.assertTrue(any(n.get('roomKey') == 'official_1' for n in graph['data']))
        stored = await self.collection.find_one({'versionKey': 'beta4'}, {'_id': 0})
        self.assertEqual(stored, self.seed)

    async def test_private_exit_nexus_is_connected_and_loadable(self):
        graph = (await rooms.handle_room_map_data({'roomMapKey': 'trickOrTreat_0'}))['data']
        nexus = next((n for n in graph['data'] if n.get('roomKey') == 'nexus'), None)
        self.assertIsNotNone(nexus, 'The Quest return portal always targets nexus')
        positions = [tuple(n['gridPosition']) for n in graph['data']]
        self.assertEqual(len(positions), len(set(positions)))
        x, y = nexus['gridPosition']
        connected = False
        for side, dx, dy, opposite in [('Up', 0, 1, 'Down'), ('Right', 1, 0, 'Left'),
                                       ('Down', 0, -1, 'Up'), ('Left', -1, 0, 'Right')]:
            neighbor = next((n for n in graph['data'] if n['gridPosition'] == [x+dx, y+dy]), {})
            if neighbor.get('roomKey') and not nexus.get('block'+side) and not neighbor.get('block'+opposite):
                connected = True
        self.assertTrue(connected, 'The return room must connect to the public layout')
        data = await rooms.handle_room_data({'roomKey': 'nexus'})
        self.assertEqual(data['roomKey'], 'nexus')
        self.assertEqual(data['roomName'], 'Public Hub')
        self.assertEqual(data['themeKey'], nexus['themeKey'])
        self.assertEqual(data['isOfficial'], 1)
        self.assertEqual(data['isCreative'], 0)
        self.assertEqual(data['noSave'], 1)
        batch = await rooms.handle_room_map_extra_properties({
            'roomKeys': ['nexus'], 'propertiesToGet': ['roomName', 'isOfficial']})
        self.assertEqual(batch['data']['responses']['nexus'],
                         {'roomName': 'Public Hub', 'isOfficial': 1})

    async def test_host_nexus_node_and_room_are_preserved(self):
        node = {'roomKey': 'nexus', 'themeKey': 'snow', 'gridPosition': [9, 9]}
        await self.collection.update_one({'versionKey': 'beta4'}, {'$push': {'data': node}})
        graph = (await rooms.handle_room_map_data({'roomMapKey': 'trickOrTreat_0'}))['data']
        self.assertEqual([n for n in graph['data'] if n.get('roomKey') == 'nexus'], [node])
        self.assertEqual(graph['version'], self.seed['version'])
        await self.rooms.insert_one({'roomKey': 'nexus', 'roomName': 'Host Hub', 'themeKey': 'snow'})
        data = await rooms.handle_room_data({'roomKey': 'nexus'})
        self.assertEqual(data['roomName'], 'Host Hub')
        self.assertEqual(data['themeKey'], 'snow')

    async def test_nexus_has_public_save_protection_only_in_buddy(self):
        from handlers import maps
        self.assertTrue(maps._is_protected_room('nexus'))
        with patch('config.UNLOCK_ALL_MODES', False):
            self.assertTrue(await maps._is_official_room('nexus'))
        with patch.object(maps, 'IS_BUDDY', False):
            self.assertFalse(maps._is_protected_room('nexus'))

    async def test_exact_legacy_key_can_be_adapted(self):
        graph = (await rooms.handle_room_map_data({'roomMapKey': 'beta4'}))['data']
        self.assertEqual(graph['data'], self.seed['data'])
        self.assertEqual(graph['uniqueID'], 'beta4')

    async def test_supplemental_room_batch_has_named_responses(self):
        await self.rooms.insert_many([
            {'roomKey': 'official_1', 'roomName': 'Host Starter House',
             'isOfficial': 1, 'privateEditorNote': 'not requested'},
            {'roomKey': 'official_0', 'roomName': 'Playground',
             'isOfficial': 1, 'visitVersion': 7}])
        result = await rooms.handle_room_map_extra_properties({
            'roomKeys': ['official_1', 'official_0', 'missing'],
            'propertiesToGet': ['roomKey', 'roomName', 'isOfficial', 'visitVersion']})
        self.assertEqual(result, {'data': {'responses': {
            'official_1': {'roomKey': 'official_1', 'roomName': 'Host Starter House',
                           'isOfficial': 1, 'visitVersion': 1},
            'official_0': {'roomKey': 'official_0', 'roomName': 'Playground',
                           'isOfficial': 1, 'visitVersion': 7}}}})

    async def test_private_world_graph_keeps_return_door_and_host_theme(self):
        await self.rooms.insert_one({'roomKey': 'p_o_tester', 'themeKey': 'snow'})
        result = await rooms.handle_room_map_data({'roomMapKey': 'p_o_tester'})
        nodes = result['data']['data']
        self.assertTrue(any(n.get('transitionKey') == 'public' for n in nodes))
        main = next(n for n in nodes if n.get('roomKey') == 'p_o_tester')
        self.assertEqual(main['themeKey'], 'snow')
        self.assertTrue(any(n.get('roomKey') == 'p_o_tester_sandbox' for n in nodes))

    async def test_missing_private_room_is_saveable_and_named_in_lobby(self):
        result = await rooms.handle_room_data({'roomKey': 'p_o_tester'})
        self.assertEqual(result['isCreative'], 1)
        self.assertEqual(result['noSave'], 0)
        batch = await rooms.handle_room_map_extra_properties({
            'roomKeys': ['p_o_tester'], 'propertiesToGet': ['roomName', 'isOfficial']})
        self.assertEqual(batch['data']['responses']['p_o_tester'],
                         {'roomName': 'Private World', 'isOfficial': 0})

    async def test_host_graph_takes_precedence_over_public_alias(self):
        graph = {'uniqueID': 'host-world', 'version': 4,
                 'data': [{'roomKey': 'host-room', 'themeKey': 'snow'}]}
        await self.collection.insert_one({'roomMapKey': 'trickOrTreat_0', 'graph': graph})
        self.assertEqual(await rooms.handle_room_map_data({'roomMapKey': 'trickOrTreat_0'}),
                         {'data': graph})

    async def test_unknown_and_missing_keys_stay_empty(self):
        for key in ['unknown_world', '']:
            self.assertEqual(await rooms.handle_room_map_data({'roomMapKey': key}),
                {'data': {'data': [], 'version': 0, 'uniqueID': key}})

    async def test_legacy_profile_keeps_previous_empty_alias_behavior(self):
        with patch('config.IS_BUDDY', False):
            for key in ['trickOrTreat_0', 'beta4']:
                self.assertEqual(await rooms.handle_room_map_data({'roomMapKey': key}),
                    {'data': {'data': [], 'version': 0, 'uniqueID': key}})
