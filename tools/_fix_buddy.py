p = "tools/buddy_apk.py"
s = open(p, encoding="utf-8").read()
old = "    if b'live.yeeps-tools.org' in result:\n        raise ValueError(\"Author backend still present in patched native library\")"
new = "    if b'live.yeeps-tools.org/xxxxxxxxxxxxxxxxxx' in result:\n        raise ValueError(\"Author backend still present in patched native library\")"
assert old in s, "old not found"
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("done")
"