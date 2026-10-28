# live79 ownership catalog

> Historical investigation notes. For current behavior and testing limits, read
> [public status](../STATUS.md) and the [project guide](../PROJECT_GUIDE.md).

The original Buddy ownership seed used1629 MapObject resource keys. That catalog
is appropriate for map-block validation but omits gadgets, cosmetics and other
patterns. The running supplied client exposes6263 unique keys through
PatternManager.GetAllPatterns, including4634 keys outside that map-block catalog.
Public dictionary keys were recovered read-only; no player credentials were used
in the catalog. The file server/seed/buddy232/patterns.json preserves those keys
in native insertion order. Source APK SHA256:
21e203194d07a20a68f37b4d34d74ab2c34e94077f38ae26d9caf90c8e37a905.

The native serialized PatternManager asset has270 datasets/4101 base patterns;
the runtime adds synthesized variants. Diagnostic parser and structured dump are
under build/refract/analysis/parse-pattern-assets.py and pattern-serialized-sets.json.
Ownership must use PatternData.key, not its itemKey/category string.

Private Worlds Warp Portal uses ownership key privateWorldsPortal and belongs to
Passageways (passageways). Its itemKey is mapObject, color purple and no custom
restriction group is set. It was already in the emulator's owned/usable catalog;
Playground play mode and inventory category matter for placing it. Wilds1 is
creative and remains the correct placement-test room.

Buddy account views now add missing catalog keys while preserving existing custom
unlocks, hidden/favorite filters and roleKeys. Existing server-role-locked BLAZE
blocks retain their role checks. New-account ownership seeds use the full catalog;
map-object validation continues to use its separate block catalog. Legacy profiles
retain their prior ownership behavior.

The user requested Wilds1 next to Playground. A backed-up Buddy-only graph edit
swapped the wild_0 and bonus_6b room references, placing Wilds at[-1,-2], left
of Playground[0,-2]. Coordinates, themes and doorway flags were preserved;
graph version increased once. No map builds or room settings were rewritten.
Wilds retains noSave1 (temporary building); use the private world for persistence.
