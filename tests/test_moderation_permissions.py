"""Buddy's untagged build needs explicit personal approval, plus bound keys."""
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from handlers import moderation
from services import device_auth


class ModerationPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, *, approved=True, key='a' * 64, tag=None, buddy=True):
        doc = {} if tag is None else {'clientBuildTag': tag}
        with patch.object(moderation, 'MOD_STAFF', {'owner'}), \
             patch.dict('os.environ', {'MOD_UNTAGGED_STAFF': 'owner' if approved else ''}), \
             patch('config.IS_BUDDY', buddy), \
             patch('services.device_auth._keys_of', AsyncMock(return_value=[device_auth.key_hash('a' * 64)])), \
             patch.object(moderation, 'accounts') as store:
            store.find_one = AsyncMock(return_value=doc)
            return await moderation._staff_check({'accountID': 'owner', '_deviceKey': key})

    async def test_explicitly_approved_untagged_buddy_headset_can_moderate(self):
        self.assertEqual(await self.check(), '')

    async def test_unapproved_untagged_build_is_still_rejected(self):
        self.assertNotEqual(await self.check(approved=False), '')

    async def test_wrong_headset_cannot_use_personal_approval(self):
        self.assertEqual(await self.check(key='b' * 64), "not from the staff member's headset")

    async def test_approval_does_not_accept_known_outdated_build_tag(self):
        self.assertEqual(await self.check(tag='obsolete.123.456'), 'not on the current game build')

    async def test_personal_untagged_approval_does_not_change_legacy_policy(self):
        self.assertEqual(await self.check(buddy=False), 'not on the current game build')
