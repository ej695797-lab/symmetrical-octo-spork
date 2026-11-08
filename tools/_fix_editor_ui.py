p = "server/static/editor_ui.html"
s = open(p, encoding="utf-8").read()

# Make photon tab always visible
old = '<!--@cut photon-->    <button data-t="photon" id="tab-photon" style="display:none">Photon</button><!--@end photon-->'
new = '<button data-t="photon" id="tab-photon">Photon</button>'
assert old in s, "old not found"
s = s.replace(old, new)

# Remove JS cut markers so photon function is always available
s = s.replace("/*@cut photon*/", "")
s = s.replace("/*@end photon*/", "")

# Remove the photon guard from the whoami response (already returns True on server side)
# Just save
open(p, "w", encoding="utf-8").write(s)
print("done, length:", len(s))
"