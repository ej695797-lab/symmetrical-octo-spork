
import os
import UnityPy

here = os.path.dirname(os.path.abspath(__file__))
data_path = os.path.join(here, "data", "data.unity3d")

print("Loading:", data_path)
env = UnityPy.load(data_path)

print("\nASSET FILES")
for name, asset in env.files.items():
    print("FILE:", name)

print("\nOBJECT COUNTS")
counts = {}
for obj in env.objects:
    name = obj.type.name
    counts[name] = counts.get(name, 0) + 1

for name, count in sorted(counts.items()):
    print(f"{name}: {count}")

print("\nRESOURCE MANAGERS")
for obj in env.objects:
    if obj.type.name == "ResourceManager":
        print("ResourceManager path ID:", obj.path_id)
        try:
            tree = obj.read_typetree()
            for key, value in tree.items():
                if key == "m_Container":
                    print("Container entries:", len(value))
                    for entry in value[:30]:
                        print(" ", entry[0])
                elif key == "m_Assets":
                    print("Assets entries:", len(value))
                else:
                    print(key, "=", str(value)[:200])
        except Exception as exc:
            print("Read error:", exc)

print("\nEXTERNAL REFERENCES")
for name, asset in env.files.items():
    for external in getattr(asset, "externals", []):
        print(name, "->", getattr(external, "name", str(external)))

print("\nFinished.")
