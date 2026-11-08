p = 'tools/buddy_apk.py'
s = open(p, encoding='utf-8').read()
old = "    old_version = _unity_string('2.32.0')\n    if settings[608:608 + len(old_version)] != old_version:\n        raise ValueError('Unexpected Unity PlayerSettings version layout')"
new = "    for _ov in ('2.32.0', '2.32.0-yeeper'):\n        old_version = _unity_string(_ov)\n        if settings[608:608 + len(old_version)] == old_version:\n            break\n    else:\n        raise ValueError('Unexpected Unity PlayerSettings version layout')"
assert old in s, "old not found"
s = s.replace(old, new)
open(p, 'w', encoding='utf-8').write(s)
print("patched buddy_apk.py")
"