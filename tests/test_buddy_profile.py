import importlib
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
os.environ['CLIENT_PROFILE'] = 'buddy232'


class BuddyProfileTests(unittest.TestCase):
    def test_defaults_use_buddy_version_and_separate_database(self):
        import config
        self.assertEqual(config.REQUIRED_CLIENT_VERSION, '2.32.0-buddy')
        self.assertEqual(config.DB_NAME, 'Yeeps232Buddy')
        self.assertTrue(config.IS_BUDDY)

    def test_account_default_containers_are_independent(self):
        from services.client_profile import account_defaults
        first, second = account_defaults(), account_defaults()
        first['completedPuzzles'].append('test')
        self.assertEqual(second['completedPuzzles'], [])
        self.assertIsInstance(second['challengeNonces'], list)
        self.assertIsInstance(second['customizedItemDatas'], list)
        self.assertIsInstance(second['lootQuotaProgress'], dict)

    def test_seed_uses_new_catalog_without_legacy_staff_grants(self):
        import json
        from services.client_profile import seed_document
        root = Path(__file__).resolve().parents[1]
        docs = json.loads((root / 'server/seed/world/globals.json').read_text())
        old = next(d for d in docs if d['key'] == 'default_account_override')
        new = seed_document('globals', old)
        self.assertEqual(new['value']['roleKeys'], [])
        self.assertIn('16x16_screen', new['value']['ownedPatterns'])
        self.assertIn('portalGun', new['value']['ownedPatterns'])
        self.assertNotIn('1x5x2_stuffedTextBlaze', new['value']['ownedPatterns'])
        self.assertNotEqual(old, new)

    def test_existing_partial_account_gains_full_catalog_without_losing_custom_data(self):
        from services.client_profile import account_view
        account = {'ownedPatterns': ['1x1_stuffed', 'host-custom-pattern'],
                   'roleKeys': [], 'ownedPatterns_hidden': ['host-custom-pattern']}
        result = account_view(account)
        for key in ['privateWorldsPortal', 'portalGun', 'detonator']:
            self.assertIn(key, result['ownedPatterns'])
        self.assertIn('host-custom-pattern', result['ownedPatterns'])
        self.assertNotIn('1x5x2_stuffedTextBlaze', result['ownedPatterns'])
        self.assertEqual(result['roleKeys'], [])
        self.assertEqual(result['ownedPatterns_hidden'], ['host-custom-pattern'])
        self.assertEqual(account['ownedPatterns'], ['1x1_stuffed', 'host-custom-pattern'])

    def test_new_room_fields_have_explicit_defaults(self):
        from services.property_defaults import default_for_property
        self.assertEqual(default_for_property('overrideMaxPlayers'), 0)
        self.assertEqual(default_for_property('overrideSpawnPosition'), [0, 0, 0])
        self.assertEqual(default_for_property('teamItemRestrictions'), {})
        self.assertEqual(default_for_property('challengeNonces'), [])


if __name__ == '__main__':
    unittest.main()
