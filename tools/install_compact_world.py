#!/usr/bin/env python3
"""Install the host-approved compact public world; leaves private saves untouched."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))


async def main():
    from config import IS_BUDDY
    if not IS_BUDDY:
        raise ValueError('Requires the buddy232 profile')
    from services.db import db, client
    from services.compact_world import install_compact_world
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    backup = ROOT / 'build/backups' / f'public-world-before-compact-{stamp}.json'
    try:
        result = await install_compact_world(db, backup, int(time.time()))
        print('Installed compact public world' if result['changed'] else 'Already installed; preserved current edits')
        print('Private data unchanged:', result['privateBefore'] == result['privateAfter'])
        if result['changed']:
            print('Backup:', result['backup'])
            print('Public rooms:', result['rooms'], 'Connected map nodes:', result['nodes'])
    finally:
        client.close()


if __name__ == '__main__':
    asyncio.run(main())
