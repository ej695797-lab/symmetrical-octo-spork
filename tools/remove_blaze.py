import zipfile, os, tempfile, shutil, sys
sys.path.insert(0, r"C:\Yeeps-2.32.0-yeeper920\tools")
import UnityPy

apk_path = r"C:\Yeeps-2.32.0-yeeper920\build\Yeeps-2.32-yeeper.apk"
out_path = r"C:\Yeeps-2.32.0-yeeper920\build\Yeeps-2.32-yeeper-noblaze.apk"

# Extract
tmp = tempfile.mkdtemp()
z = zipfile.ZipFile(apk_path)
data_bytes = z.read("assets/bin/Data/data.unity3d")
data_path = os.path.join(tmp, "data.unity3d")
with open(data_path, "wb") as f:
    f.write(data_bytes)
print("extracted data.unity3d:", len(data_bytes))

# Load and remove blaze entries
env = UnityPy.load(data_path)
rm = next(o for o in env.objects if o.type.name == "ResourceManager")
rm_tree = rm.read_typetree()
cont = rm_tree["m_Container"]
before = len(cont)
rm_tree["m_Container"] = [(k, v) for k, v in cont if "blaze" not in k.lower()]
after = len(rm_tree["m_Container"])
rm.save_typetree(rm_tree)
print(f"removed {before - after} blaze entries ({before} -> {after})")

# Save data.unity3d
new_data = env.file.save(packer="lz4")
new_data_path = os.path.join(tmp, "data_new.unity3d")
with open(new_data_path, "wb") as f:
    f.write(new_data)
print("saved new data.unity3d:", len(new_data))

# Repack APK
with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in z.infolist():
        if item.filename == "assets/bin/Data/data.unity3d":
            zout.write(new_data_path, item.filename)
        else:
            zout.writestr(item, z.read(item.filename))
print("repacked APK:", out_path, os.path.getsize(out_path))
z.close()