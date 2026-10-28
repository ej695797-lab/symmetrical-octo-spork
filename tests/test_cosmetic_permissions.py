"""Personal full-palette access must not change other accounts' policy."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from services import hair_colors


class CosmeticPermissionTests(unittest.TestCase):
    def test_allowlisted_account_keeps_full_hair_palette(self):
        cosmetics = ['hairBoy6[purple]', 'hairGirl5[red]', 'hat[blue]']
        with patch.dict('os.environ', {'UNRESTRICTED_COSMETIC_ACCOUNTS': 'owner,other'}):
            self.assertEqual(hair_colors.clamp_active_cosmetics(cosmetics, 'owner'), cosmetics)

    def test_other_accounts_still_use_existing_palette_policy(self):
        with patch.dict('os.environ', {'UNRESTRICTED_COSMETIC_ACCOUNTS': 'owner'}):
            self.assertEqual(hair_colors.clamp_active_cosmetics(['hairBoy6[purple]'], 'visitor'),
                             ['hairBoy6[brown]'])
            self.assertEqual(hair_colors.clamp_active_cosmetics(['hairBoy6[purple]']),
                             ['hairBoy6[brown]'])


if __name__ == '__main__':
    unittest.main()
