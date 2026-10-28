"""Modern Lambda map reads must agree with the existing import/save workflow."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from mongomock_motor import AsyncMongoMockClient
from handlers import maps


class BuddyMapFetchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient()['map_fetch_tests']
        import services.db as storage
        self.patches = [patch.object(maps, 'maps', self.db.maps),
                        patch.object(storage, 'imported_maps', self.db.imported_maps),
                        patch.object(maps, 'IS_BUDDY', True)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_lambda_can_read_an_editor_import(self):
        await self.db.maps.insert_one({'mapKey': 'official_1$current',
            'saveData': 'old-snapshot', 'mapDataVersion': 3, 'gameSessionID': '11'})
        import services.db as storage
        with patch.object(storage, 'imported_maps', self.db.imported_maps):
            await self.db.imported_maps.insert_one({'roomKey': 'official_1',
                'saveData': 'imported-opaque-map', 'saveInternalStates': 'states'})
            result = await maps.handle_fetch_map_data({'roomKey': 'official_1',
                'targetSessionID': 42, 'saveOffset': 0})
        self.assertEqual(result['saveData'], 'imported-opaque-map')
        self.assertEqual(result['saveInternalStates'], 'states')
        self.assertEqual(result['mapDataVersion'], 3)

    async def test_requested_session_chunks_win_over_current_snapshot(self):
        await self.db.maps.insert_one({'mapKey': 'test_room$current',
            'saveData': 'other-session', 'mapDataVersion': 3})
        for offset in [0, 1]:
            await self.db.maps.insert_one({'mapKey': f'test_room$map_42_save{offset}',
                'saveData': f'chunk-{offset}', 'mapDataVersion': 3,
                'saveInternalStates': f'states-{offset}'})
            result = await maps.handle_fetch_map_data({'roomKey': 'test_room',
                'targetSessionID': 42, 'saveOffset': offset})
            self.assertEqual(result['saveData'], f'chunk-{offset}')
            self.assertEqual(result['saveInternalStates'], f'states-{offset}')
        end = await maps.handle_fetch_map_data({'roomKey': 'test_room',
            'targetSessionID': 42, 'saveOffset': 2})
        self.assertEqual(end['saveData'], '')

    async def test_fallback_snapshot_does_not_continue_with_orphan_chunk(self):
        await self.db.maps.insert_many([
            {'mapKey': 'test_room$current', 'saveData': 'snapshot', 'mapDataVersion': 3},
            {'mapKey': 'test_room$map_42_save1', 'saveData': 'orphan', 'mapDataVersion': 3}])
        result = await maps.handle_fetch_map_data({'roomKey': 'test_room',
            'targetSessionID': 42, 'saveOffset': 1})
        self.assertEqual(result['saveData'], '')

    async def test_current_save_for_requested_session_wins_over_old_import(self):
        await self.db.maps.insert_one({'mapKey': 'test_room$current',
            'saveData': 'new-save', 'mapDataVersion': 3, 'gameSessionID': '42'})
        await self.db.imported_maps.insert_one({'roomKey': 'test_room', 'saveData': 'old-import'})
        result = await maps.handle_fetch_map_data({'roomKey': 'test_room',
            'targetSessionID': 42, 'saveOffset': 0})
        self.assertEqual(result['saveData'], 'new-save')

    async def test_missing_terrain_selects_native_procedural_fallback(self):
        from services.client_profile import room_defaults
        self.assertEqual(room_defaults()['isNoTerrain'], 0)
        for offset in [0, 1]:
            result = await maps.handle_fetch_map_data({'roomKey': 'official_1',
                'isTerrain': 1, 'saveOffset': offset})
            self.assertEqual(result, {'mapDataVersion': 1, 'saveData': ''})

    async def test_saved_terrain_uses_its_own_version_and_bytes(self):
        await self.db.maps.insert_one({'mapKey': 'test_room$current',
            'saveData': 'blocks', 'mapDataVersion': 3,
            'terrainSaveData': 'opaque-terrain', 'terrainDataVersion': 1})
        result = await maps.handle_fetch_map_data({'roomKey': 'test_room', 'isTerrain': 1})
        self.assertEqual(result, {'mapDataVersion': 1, 'saveData': 'opaque-terrain'})
