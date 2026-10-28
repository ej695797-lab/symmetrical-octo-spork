#!/usr/bin/env python3
"""Copy game data and streamed resources out of a legacy or Buddy APK.

    python tools/data/extract_data.py Yeeps-1.31.0-blaze-kit2.apk data

Writes data.unity3d and every top-level .resource/.resS streamed resource.
See tools/data/README.md for Buddy script tests. Legacy APK packaging uses
tools/configure_apk.py ... --data data; the Buddy packager has no --data option.
"""
import os
import sys
import zipfile

FILES = ("assets/bin/Data/data.unity3d", "assets/bin/Data/sharedassets1.resource")


def main(apk: str, out: str) -> None:
    os.makedirs(out, exist_ok=True)
    with zipfile.ZipFile(apk) as z:
        names = z.namelist()
        if FILES[0] not in names:
            raise ValueError('APK has no supported data.unity3d bundle')
        files = [FILES[0]] + sorted(name for name in names
                if name.startswith('assets/bin/Data/')
                and '/' not in name[len('assets/bin/Data/'):]
                and name.endswith(('.resource', '.resS')))
        for name in files:
            dest = os.path.join(out, os.path.basename(name))
            with z.open(name) as src, open(dest, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
            print(f"{dest}  ({os.path.getsize(dest) // (1 << 20)} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python tools/data/extract_data.py <apk> <output folder>")
    main(sys.argv[1], sys.argv[2])
