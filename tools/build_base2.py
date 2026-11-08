import os, sys, tempfile, zipfile, shutil, subprocess

apk_path = r"C:\Users\hayden&eth\Downloads\base.apk"
out_path = r"C:\Yeeps-2.32.0-yeeper920\build\Yeeps-base-noblaze.apk"
signed_path = r"C:\Yeeps-2.32.0-yeeper920\build\Yeeps-base-noblaze-signed.apk"
tmp = tempfile.mkdtemp()

# 1. Create libblazeconfig.so
config_content = """# Yeeps 2.32.0-yeeper920 self-host settings
server=https://yeeper920-2-32-0.onrender.com
photon_app_id=a279e749-3e24-464c-9336-f3a412bdfdbc
photon_voice_app_id=2d9a7f4b-96b8-4305-b87f-0ea0aa722d1f
photon_region=
photon_app_version=2.32.0_2.45
discord=https://discord.gg/twD66vfbeA
"""
config_path = os.path.join(tmp, "libblazeconfig.so")
with open(config_path, "w") as f:
    f.write(config_content)

# 2. Extract and modify data.unity3d (remove blaze blocks if any)
z = zipfile.ZipFile(apk_path)
data_bytes = z.read("assets/bin/Data/data.unity3d")
data_path = os.path.join(tmp, "data.unity3d")
with open(data_path, "wb") as f:
    f.write(data_bytes)

import UnityPy
env = UnityPy.load(data_path)
rm = next(o for o in env.objects if o.type.name == "ResourceManager")
rm_tree = rm.read_typetree()
cont = rm_tree["m_Container"]
before = len(cont)
rm_tree["m_Container"] = [(k, v) for k, v in cont if "blaze" not in k.lower()]
after = len(rm_tree["m_Container"])
rm.save_typetree(rm_tree)
print(f"removed {before - after} blaze entries ({before} -> {after})")

new_data = env.file.save(packer="lz4")
new_data_path = os.path.join(tmp, "data_new.unity3d")
with open(new_data_path, "wb") as f:
    f.write(new_data)

# 3. Repack APK
with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in z.infolist():
        if item.filename == "assets/bin/Data/data.unity3d":
            zout.write(new_data_path, item.filename)
        elif item.filename == "lib/arm64-v8a/libblazeconfig.so":
            pass
        elif item.filename == "lib/arm64-v8a/libBNMExplorer.so":
            pass
        else:
            zout.writestr(item, z.read(item.filename))
    # Add libblazeconfig.so
    zout.write(config_path, "lib/arm64-v8a/libblazeconfig.so")
    # Add libBNMExplorer.so
    bnm_path = r"C:\Yeeps-2.32.0-yeeper920\build\Yeeps-2.32-yeeper\lib\arm64-v8a\libBNMExplorer.so"
    if os.path.exists(bnm_path):
        zout.write(bnm_path, "lib/arm64-v8a/libBNMExplorer.so")
        print("added libBNMExplorer.so")
print("repacked:", out_path, os.path.getsize(out_path))
z.close()

# 4. Sign
BT = r"C:\Users\hayden&eth\AppData\Local\Android\Sdk\build-tools\36.0.0"
KEY = r"C:\Yeeps-2.32.0-yeeper920\tools\.keys\yeeps-buddy.keystore"
PASS = open(KEY + ".pass").read().strip()
aligned = os.path.join(tmp, "aligned.apk")
subprocess.run([os.path.join(BT, "zipalign.exe"), "-p", "-f", "4", out_path, aligned], check=True)
print("aligned")
subprocess.run(["java", "-jar", os.path.join(BT, "lib", "apksigner.jar"),
                "sign", "--ks", KEY, "--ks-pass", f"pass:{PASS}",
                "--out", signed_path, aligned], check=True)
print("signed:", signed_path, os.path.getsize(signed_path))
