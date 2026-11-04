
import zipfile
from pathlib import Path
from buddy_apk import Elf

apk = Path(r"C:\Yeeps-2.32.0-yeeper920\build\Yeeps-2.32.0-yeeper.apk")

with zipfile.ZipFile(apk) as z:
    data = z.read("lib/arm64-v8a/libBNMExplorer.so")

elf = Elf(data)

required = [
    "_ZL15kOurPhotonAppId",
    "_ZL14kOurVoiceAppId",
    "_ZL9kSelfBase",
    "_ZL11kDevlogHost",
    "_ZL8kCosHost",
    "_ZL8kBuildId",
    "_ZL19InstallRemoteClientv",
    "_ZL17InstallRemoteLogsv",
    "_ZL18DevlogUploadThreadv",
    "_ZL11DevlogFlushv",
]

for name in required:
    print(("FOUND  " if name in elf.symbols else "MISSING ") + name)


print("\n--- ELF PROGRAM HEADERS ---")
for i, h in enumerate(elf.programs):
    print(
        f"{i}: type={h[0]} flags={h[1]} "
        f"offset={hex(h[2])} vaddr={hex(h[3])} "
        f"filesz={hex(h[5])} memsz={hex(h[6])} "
        f"align={hex(h[7])}"
    )

print("\n--- SYMBOL DETAILS ---")
for name in required:
    if name in elf.symbols:
        print(name, elf.symbols[name][0])

print("\n--- VERSION STRINGS ---")
for marker in [b"blaze", b"live", b"yeeps"]:
    print(marker.decode(), data.count(marker))
