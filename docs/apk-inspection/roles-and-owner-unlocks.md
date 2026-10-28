# Modern roles and scoped host permissions

The fingerprinted live79 RoleManager (data.unity3d, level2 pathID509,
MonoScript globalgamemanagers.assets pathID5067) contains 11 global roles:
`developer`, `tester`, `moderator`, `contentCreator`, `superStar`, `alpha`,
`internal`, `bamWinner`, `popUpWinner`, `maker`, `reviewer`. The array parses to
488 bytes. Following `cwAdmin`, `cwStaff`, `cwVIP` records are room-context roles;
do not put them in every account's global `roleKeys`. Old developer_senior and
moderator_junior names do not describe this asset.

Normal Buddy accounts have no staff roles by default. Grant privileges only
when requested by the current host, to a verified intended account, with backups.
Do not distribute personal account allowlists, live grant scripts or backups.
Pattern ownership, global roles, bundle entitlements and room staff memberships
are distinct; do not infer permissions from inventory ownership.

Private `server/.env` settings:

- `MOD_STAFF`: accounts permitted to moderate, with bound-headset proof.
- `MOD_UNTAGGED_STAFF`: explicit Buddy-only permission for approved untagged
  client installs. Still requires MOD_STAFF and bound-headset verification;
  an explicitly rejected build tag remains rejected.
- `UNRESTRICTED_COSMETIC_ACCOUNTS`: scoped permission to preserve all equipped
  hair colors through cosmetic and Dynamo write paths.

Blaze holder membership is required in addition to the `blaze` role. Preserve
saved maps, equipped cosmetics and hidden-pattern filters during account changes.
The full recovered pattern catalog does not establish support for every modern
remote-backed feature or bundle.

BNM Explorer at http://localhost:8080 and the news-board menu button are separate
from the game's role-controlled moderator interface. Native class names include
MobileSettingsMenuContent_ModeratorMenu and MobileModeratorMenuButtonActiveController.
Role-controlled UI visibility and operation still need headset verification.
Do not invoke gameplay methods to force menus or navigation; see docs/STATUS.md.
