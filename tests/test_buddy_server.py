"""Exercise real handlers and ASGI routing against an isolated Mongo test double."""
import importlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
os.environ['CLIENT_PROFILE'] = 'buddy232'

from mongomock_motor import AsyncMongoMockClient
import services.db as storage

# Install the isolated store before handlers import collection references.
storage.client = AsyncMongoMockClient()
storage.db = storage.client['buddy_tests']
for attr in ['accounts', 'rooms', 'room_maps', 'maps', 'player_statuses',
             'bundles', 'debug_logs', 'imported_maps']:
    setattr(storage, attr, storage.db[attr])
storage.globals_col = storage.db['globals']
for table, key in storage.TABLE_KEY_FIELDS.items():
    collection = {'accountID': 'accounts', 'roomKey': 'rooms', 'versionKey': 'room_maps',
                  'mapKey': 'maps', 'key': 'globals_col', 'bundleKey': 'bundles'}[key]
    if table == 'G2_PlayerStatuses':
        collection = 'player_statuses'
    storage.TABLE_MAP[table] = getattr(storage, collection)

from seed.load_world import load_world
from handlers.login import handle_login
from handlers.maps import handle_fetch_map_data, handle_save_room_map
from services.dynamo_ops import handle_get_item, handle_batch_get_item


class BuddyServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Other isolated test modules may import handlers before this module
        # installs its store. Patch captured collection references per test.
        from handlers import maps as map_handlers
        self.enterContext(patch.object(map_handlers, 'maps', storage.maps))
        self.enterContext(patch.object(map_handlers, 'rooms', storage.rooms))
        for name in await storage.db.list_collection_names():
            await storage.db[name].delete_many({})
        from services import device_auth, role_locks
        device_auth._cache.clear()
        role_locks._blaze.update(ids=set(), ts=0)
        await load_world()

    async def test_login_matches_client_credential_vectors_and_plural_nonce(self):
        result = await handle_login({'accountID': 'o_12345', 'oculusID': 'BuddyTester',
                                     '_clientIP': '127.0.0.1', '_deviceKey': 'a' * 64,
                                     'trueVersion': '2.32.0-buddy',
                                     'password': 'different-login-password',
                                     'oculusNonce': 'buddy-test-password'})
        data = result['data']
        self.assertEqual(data['applicationVersion'], '2.32.0-buddy')
        self.assertTrue(result['accessKey'])
        self.assertTrue(result['challengeNonces'])
        # Captured from live79's encryption helper using these public fake
        # credentials and this fixed test nonce, never a player secret. The
        # request password is deliberately different: it is not the AES suffix.
        self.assertEqual(data['e_accessKey'],
            'vVtEQaWXuLg/Mm71JzrL3jWeSc35 ojOZPyfbOmnsyHRBgnl/nD49ZN7OtMolxFz')
        self.assertEqual(data['e_secretKey'],
            'qTGa25drg590urNcl7CDTrZZbR4F7k4qL7wkpbfI1W/Oay9H0QbXlmQ0nncneAWRzBxCHdWkUG1Iroed3PefZG3DzoukS0xZVzFUeLlqQNLLfoYhWTfki4jjYhY FzDh')
        self.assertEqual(result['e_accessKey'], data['e_accessKey'])
        self.assertEqual(result['e_secretKey'], data['e_secretKey'])
        self.assertNotIn('deviceKeys', data)
        self.assertEqual(data['roleKeys'], [])
        self.assertIn('16x16_screen', data['ownedPatterns'])

    async def test_login_without_projection_keeps_safe_identity_fields(self):
        await storage.accounts.insert_one({'accountID': 'o_fields',
            'displayName': 'FieldTester', 'mobileCode': 'private-mobile-secret'})
        result = await handle_login({'accountID': 'o_fields', 'oculusID': 'FieldTester',
            '_clientIP': '127.0.0.1', '_deviceKey': 'a' * 64,
            'trueVersion': '2.32.0-buddy'})
        # live79 does not send propertiesToGet. Its account parser still reads
        # these string fields; omitting them must not leak stored mobile secrets.
        self.assertEqual(result['data']['accountID'], 'o_fields')
        self.assertEqual(result['data']['mobileCode'], '0000')
        self.assertNotIn('private-mobile-secret', json.dumps(result))

    async def test_customized_items_are_arrays_for_fresh_and_existing_accounts(self):
        # live79 accessor 0x50a4df0 calls JSONNode.AsArray, then dereferences it.
        # Earlier Buddy defaults persisted {} in both the override and accounts.
        await storage.globals_col.update_one({'key': 'default_account_override'},
            {'$set': {'value.customizedItemDatas': {}}})
        await storage.accounts.insert_one({'accountID': 'o_old_items',
            'displayName': 'Existing', 'customizedItemDatas': {}})
        custom_items = [{'itemKey': 'example', 'data': 'opaque-customization'}]
        await storage.accounts.insert_one({'accountID': 'o_custom_items',
            'displayName': 'Customized', 'customizedItemDatas': custom_items})
        for account_id, expected in [('o_new_items', []), ('o_old_items', []),
                                     ('o_custom_items', custom_items)]:
            with self.subTest(account_id=account_id):
                result = await handle_login({'accountID': account_id,
                    'oculusID': 'ArrayTester', '_clientIP': '127.0.0.1',
                    '_deviceKey': 'a' * 64, 'trueVersion': '2.32.0-buddy'})
                self.assertEqual(result['data']['customizedItemDatas'], expected)
                raw, _ = await handle_get_item({'TableName': 'G2_Accounts',
                    'Key': {'accountID': {'S': account_id}},
                    'ProjectionExpression': 'customizedItemDatas'})
                self.assertEqual(json.loads(raw)['Item']['customizedItemDatas'],
                    {'L': []} if not expected else {'L': [{'M': {
                        'itemKey': {'S': 'example'},
                        'data': {'S': 'opaque-customization'}}}]})
        stored = await storage.accounts.find_one({'accountID': 'o_custom_items'})
        self.assertEqual(stored['customizedItemDatas'], custom_items)

    async def test_projection_returns_only_requested_typed_room_fields(self):
        raw, _ = await handle_get_item({'TableName': 'G2_Rooms',
            'Key': {'roomKey': {'S': 'official_1'}},
            'ProjectionExpression': '#players, overrideSpawnPosition',
            'ExpressionAttributeNames': {'#players': 'overrideMaxPlayers'}})
        item = json.loads(raw)['Item']
        self.assertEqual(set(item), {'overrideMaxPlayers', 'overrideSpawnPosition'})
        self.assertEqual(item['overrideMaxPlayers'], {'N': '0'})
        self.assertEqual(item['overrideSpawnPosition'], {'L': [{'N': '0'}] * 3})

    async def test_missing_map_has_supported_format_and_chunk_terminator(self):
        from services.mapsync_serializer import serialize_blocks_spam
        result = await handle_fetch_map_data({'roomKey': 'official_1', 'saveOffset': 0})
        self.assertEqual(result['mapDataVersion'], 3)
        self.assertEqual(result['saveData'], serialize_blocks_spam([]))
        end = await handle_fetch_map_data({'roomKey': 'official_1', 'saveOffset': 1})
        self.assertEqual(end['mapDataVersion'], 3)
        self.assertEqual(end['saveData'], '')

    async def test_batch_projection_fills_new_account_defaults(self):
        await storage.accounts.insert_one({'accountID': 'o_existing', 'displayName': 'Existing'})
        raw, _ = await handle_batch_get_item({'RequestItems': {'G2_Accounts': {
            'Keys': [{'accountID': {'S': 'o_existing'}}],
            'ProjectionExpression': 'completedPuzzles, featureGrants'}}})
        item = json.loads(raw)['Responses']['G2_Accounts'][0]
        self.assertEqual(item, {'completedPuzzles': {'L': []}, 'featureGrants': {'L': []}})

    async def test_seed_never_overwrites_host_changes(self):
        await storage.rooms.update_one({'roomKey': 'official_1'}, {'$set': {'roomName': 'Host Room'}})
        await load_world()
        self.assertEqual((await storage.rooms.find_one({'roomKey': 'official_1'}))['roomName'], 'Host Room')

    async def test_spam_fetch_preserves_opaque_save_and_internal_states(self):
        # Intentionally opaque to the old decoder; newer clients may extend it.
        blob = 'U1BBTQ' + 'A' * 160
        await storage.maps.insert_one({'mapKey': 'p_o_12345$current', 'saveData': blob,
            'saveInternalStates': 'future-format-states', 'mapDataVersion': 9})
        response = await handle_fetch_map_data({'roomKey': 'p_o_12345'})
        self.assertEqual(response['saveData'], blob)
        self.assertEqual(response['saveInternalStates'], 'future-format-states')
        self.assertEqual(response['mapDataVersion'], 9)

    async def test_private_map_save_and_both_fetch_paths_preserve_newer_blobs(self):
        from services.dynamo_ops import _map_doc
        blob = 'U1BBTQ' + 'A' * 160
        response = await handle_save_room_map({'accountID': 'o_saver', 'roomKey': 'p_o_saver',
            'mapData': blob, 'internalStates': 'opaque-internal-states',
            'mapDataVersion': 9, 'gameSessionID': '123'})
        self.assertEqual(response['success'], 1)
        stored = await storage.maps.find_one({'mapKey': 'p_o_saver$current'})
        self.assertEqual(stored['saveData'], blob)
        self.assertEqual(stored['saveInternalStates'], 'opaque-internal-states')
        fetched = await handle_fetch_map_data({'roomKey': 'p_o_saver'})
        dynamo = await _map_doc('p_o_saver$map_123_save0')
        self.assertEqual(fetched['saveData'], blob)
        self.assertEqual(dynamo['saveData'], blob)
        self.assertEqual(dynamo['saveInternalStates'], 'opaque-internal-states')

    async def test_asgi_prefix_login_and_health(self):
        import httpx
        from main import app
        transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 1234))
        async with httpx.AsyncClient(transport=transport, base_url='http://buddy') as client:
            health = await client.get('/health')
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()['clientVersion'], '2.32.0-buddy')
            result = await client.post('/xxxxxxxxxxxxxxxxxx/k/' + 'b' * 64 +
                '/c/buddy1.0000000000000000.0000000000000000/questLogIntoAccount',
                json={'accountID': 'o_98765', 'oculusID': 'RouteTester'})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['data']['displayName'], 'RouteTester')


if __name__ == '__main__':
    unittest.main()
