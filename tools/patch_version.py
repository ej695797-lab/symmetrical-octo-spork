import os, sys, zipfile, tempfile, struct
sys.path.insert(0, r"C:\yeeps\tools")
from configure_apk import patch_axml_strings

SRC = r"C:\Yeeps-2.32.0-yeeper920\build\Yeeps-live93-noblaze.apk"
DST = r"C:\Yeeps-2.32.0-yeeper920\build\Yeeps-2.32-yeeper920.apk"
BT = r"C:\Users\hayden&eth\AppData\Local\Android\Sdk\build-tools\36.0.0"
KEY = r"C:\Yeeps-2.32.0-yeeper920\tools\.keys\yeeps-buddy.keystore"
PASS = open(KEY + ".pass").read().strip()

zin = zipfile.ZipFile(SRC)
manifest = bytearray(zin.read("AndroidManifest.xml"))
data, _ = patch_axml_strings(manifest, {"2.32.0": "2.32.0-yeeper920"})
print("patched manifest")

tmpdir = tempfile.mkdtemp(dir=r"C:\Yeeps-2.32.0-yeeper920\build")
raw = os.path.join(tmpdir, "raw.apk")
with zipfile.ZipFile(raw, "w") as zout:
    for item in zin.infolist():
        n = item.filename
        if n.startswith("META-INF/") and n.rsplit(".", 1)[-1].upper() in ("SF", "RSA", "DSA", "EC", "MF"):
            continue
        d = bytes(manifest) if n == "AndroidManifest.xml" else zin.read(item)
        zout.writestr(item, d, compress_type=item.compress_type)
zin.close()

aligned = os.path.join(tmpdir, "aligned.apk")
import subprocess
subprocess.run([os.path.join(BT, "zipalign.exe"), "-p", "-f", "4", raw, aligned], check=True)
subprocess.run(["java", "-jar", os.path.join(BT, "lib", "apksigner.jar"),
                "sign", "--ks", KEY, "--ks-pass", f"pass:{PASS}",
                "--out", DST, aligned], check=True)
print("signed:", DST, os.path.getsize(DST))