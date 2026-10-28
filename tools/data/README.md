# Custom content for Buddy 2.32

These are offline asset tools carried forward from Blaze. They do not update an
installed app, build an APK, grant ownership, contact the server, or change maps.
Use disposable extracted data and keep the original APK. Headset rendering and
playback require a separate APK/in-game test and have not been verified here.

Activate the Python environment described in the main README, then install:

```bash
python -m pip install -r tools/data/requirements.txt
```

The examples below use Bash line continuations; on Windows Command Prompt, put
each command on one line and replace paths with your own quoted paths.

## Extract data

```bash
python tools/data/extract_data.py /path/to/live79-blaze.apk build/content/source
```

Extraction now includes **all** top-level `.resource` / `.resS` files. Buddy's
record clips live in `sharedassets2.resource`; the old tool extracted only
`sharedassets1.resource`. Keep the resources alongside `data.unity3d`.

## Add a named GLB block

```bash
python tools/data/add_model_block.py \
  build/content/source/data.unity3d build/content/fumo/data.unity3d \
  --model /path/to/fumo.glb \
  --key 1x1_stuffedFumo --name 'Reimu Fumo'
```

The key is the stable identity used by patterns and saved maps. `--name` is the
human-readable inventory display name. Buddy's inspected `PatternData` explicitly
stores both; names must not replace keys. This field also existed in the legacy
picture tool, although its old layouts/templates are unsuitable for this model.

The default block occupies one 0.5-meter grid cell. `--size X Y Z` changes the
footprint; geometry is centered and uniformly fitted inside it. `--cost` sets
cotton cost (default zero). The block is added to Pillows with a box collider and
pillow sounds. The collider owner inherits the ordinary pillow’s `Destructable`
tag and Ground layer so it participates in block selection/pickup; the Cucumber
Cat visual template itself is untagged and must not supply that tag. The importer bakes node transforms, converts handedness/winding,
transforms normals and UVs, and embeds the original texture with its filtering
and wrapping. Opaque and alpha-cutout (MASK) materials are supported; Fumo keeps
its transparent cutouts and nearest-neighbor pixel filtering. A child renderer
keeps the texture separate from the root's paint material; no legacy native
picture hook is required. Actual palette/placement behavior still needs a Quest
test. Original items remain unchanged.

Supported inputs: static GLB 2, embedded buffer/image, triangle meshes, normals,
UV0, a single base-color textured material. Skins, animations, compressed/required
extensions, sparse/normalized accessors, multiple materials, texture transforms
base-color tints and transparent BLEND materials are rejected. This is intentionally a focused importer for
the supplied Fumo, not a full glTF renderer. Output must differ from the input.
A `.block.json` sidecar records names, size, mesh counts and source attribution.

Before the next step, copy **all** the extracted resource files into the folder
holding the modified `data.unity3d`. Resources themselves are unchanged by this
block tool. The local test folders use links to the immutable source copies.

## Add a separate named music disc

```bash
python tools/data/add_music_disc.py build/content/fumo --list
python tools/data/add_music_disc.py build/content/fumo build/content/combined \
  --audio "/path/to/your-song.mp3" \
  --key record_verity --name "It's Me, It's Verity" --artist 'Horror Skunx'
```

The output folder must be new. The script converts local audio with ffmpeg to
44.1 kHz mono PCM16 FSB5, creates a new AudioClip, appends its bank to the resource
referenced by the source clip, then registers a new routable audio entry, record
entry and inventory pattern. Audio IDs and record IDs are distinct. Existing
record IDs, songs, titles and serialized trailing settings stay unchanged.
Conversion and validation finish before any output folder is published.

`--label image.png` optionally replaces the top-left label quadrant of a cloned
record-art atlas. Without it, the separate new artwork asset retains Sleepover's
label style; the new inventory/song title identifies the disc. `--cost` defaults
to zero. The output includes all streamed resources and `custom-disc.json`.

Audio input is a local file you supply. Downloading audio is separate from these
tools; no browser cookies or external account credentials are needed.

## Results from the supplied files

The development sample used a Reimu Fumo GLB (1,182 vertices, 556 triangles,
128x128 texture) and a separate Verity record. These inputs and generated assets
are not distributed in this repository. Generate your own `build/content/combined`
folder with the commands above; historical test folders are not setup dependencies.

The generated Fumo root tag/collider has offline regression coverage. Placement
was reported on Quest; pickup after the tag repair and disc playback/save/reload
still need headset testing. Set `BUDDY_CUSTOM_DATA` to your complete generated
Fumo folder to enable its interaction-asset regression check.

The old `add_picture_block.py` and `bake_disk.py` retain their legacy interfaces.
Use the new model/record tools for this Buddy test. In particular, `bake_disk.py`
replaces an existing song; it does not add a separate record.

## Rebuild Buddy with the edited data

The Buddy builder supports a complete custom-data folder:

```bash
python tools/configure_buddy_apk.py \
  --apk /path/to/live79-blaze.apk \
  --server https://YOUR-SERVER-HOST \
  --photon-app-id YOUR-REALTIME-ID --photon-voice-app-id YOUR-VOICE-ID \
  --data build/content/combined --out build/Yeeps-2.32.0-buddy-custom.apk
```

Use the immutable inspected source APK and the existing Buddy keystore. The
builder overlays the edited bundle and all matching streamed resources, patches
Buddy/host settings, preserves compression, then aligns, signs and verifies.
Incomplete folders and unknown resource files are refused. The generated APK
metadata records the input custom-data hashes.

The custom pattern keys also need to be in each updated player's `ownedPatterns`;
the block key belongs in `valid_mapobject_keys` for map imports. Make targeted,
backed-up grants only to accounts using matching APKs. Older APKs cannot resolve
unknown custom patterns.

Install over Buddy with `adb install -r build/Yeeps-2.32.0-buddy-custom.apk`.
Do not uninstall Buddy or original Yeeps. App installation and static asset
checks do not establish in-game rendering, placement, persistence or playback.

## Model attribution

Reimu Fumo Doll [Low-Poly] by Embur:
https://sketchfab.com/3d-models/reimu-fumo-doll-low-poly-492b33b4de924595961ee9c14e0361b9
Licensed CC BY 4.0: https://creativecommons.org/licenses/by/4.0/
Imported from the user-provided GLB; transformed and scaled for the test block.
