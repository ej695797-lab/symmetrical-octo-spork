import os, subprocess, sys, tempfile, zipfile
BT = r"C:\Users\hayden&eth\AppData\Local\Android\Sdk\build-tools\36.0.0"
KEY = r"C:\Yeeps-2.32.0-yeeper920\tools\.keys\yeeps-buddy.keystore"
PASS = open(KEY + ".pass").read().strip()
SRC = r"C:\Yeeps-2.32.0-yeeper920\build\Yeeps-2.32-yeeper-noblaze.apk"
DST = r"C:\Yeeps-2.32.0-yeeper920\build\Yeeps-2.32-yeeper-noblaze-signed.apk"
tmpdir = tempfile.mkdtemp(dir=r"C:\Yeeps-2.32.0-yeeper920\build")
aligned = os.path.join(tmpdir, "aligned.apk")
subprocess.run([os.path.join(BT, "zipalign.exe"), "-p", "-f", "4", SRC, aligned], check=True)
print("aligned")
subprocess.run(["java", "-jar", os.path.join(BT, "lib", "apksigner.jar"),
                "sign", "--ks", KEY, "--ks-pass", f"pass:{PASS}",
                "--out", DST, aligned], check=True)
print("signed", DST, os.path.getsize(DST))