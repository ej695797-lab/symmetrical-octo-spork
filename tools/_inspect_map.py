import json, os
dl = r"C:\Users\hayden&eth\Downloads"
p = os.path.join(dl, "yeeps_map_blocks.json")
d = json.load(open(p, encoding="utf-8"))
print("type:", type(d).__name__)
if isinstance(d, dict):
    print("keys:", list(d.keys())[:10])
    if "blocks" in d:
        print("blocks:", len(d["blocks"]))
        print("sample:", d["blocks"][0])
    if "roomKey" in d:
        print("roomKey:", d["roomKey"])
elif isinstance(d, list):
    print("len:", len(d))
    print("sample:", d[0] if d else None)