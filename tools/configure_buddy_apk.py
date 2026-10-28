#!/usr/bin/env python3
"""Configure the inspected live79 APK as 2.32.0-buddy, align, sign and verify it.

Requires tools/requirements-buddy-apk.txt, Java, zipalign and apksigner.
The original APK stays unchanged. Photon IDs must belong to your host.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import zipfile

from buddy_apk import (AndroidXml, SOURCE_APK_SHA256, VERSION, PACKAGE, APP_LABEL, patch_manifest, patch_resources,
                       patch_native, patch_network_security, patch_unity,
                       unity_configuration, validate_settings)
from configure_apk import find_tool, ensure_key, KEY_DIR

LIB = 'lib/arm64-v8a/libBNMExplorer.so'
DATA = 'assets/bin/Data/data.unity3d'


def custom_data_entries(source_zip: zipfile.ZipFile, data_dir: Path) -> dict:
    """A complete offline asset set; never accept native/package replacements."""
    data_dir = Path(data_dir)
    names = [entry for entry in source_zip.namelist()
             if entry.startswith('assets/bin/Data/')
             and '/' not in entry[len('assets/bin/Data/'):]
             and (entry == DATA or entry.endswith(('.resource', '.resS')))]
    if DATA not in names:
        raise ValueError('Source APK has no data.unity3d')
    result = {}
    for name in names:
        path = data_dir / name.rsplit('/', 1)[-1]
        if not path.is_file():
            raise ValueError(f'Custom data is incomplete: missing {path.name}')
        result[name] = path
    extras = [p.name for p in data_dir.iterdir()
              if p.is_file() and p.suffix in {'.resource', '.resS'}
              and 'assets/bin/Data/' + p.name not in result]
    if extras:
        raise ValueError(f'Custom data contains unknown resource files: {extras}')
    return result


def rewrite_apk(source: Path, output: Path, server: str, realtime: str, voice: str,
                region: str = '', data_dir: Path | None = None) -> None:
    validate_settings(server, realtime, voice)
    if source.resolve() == output.resolve():
        raise ValueError('Output must differ from the immutable source APK')
    with source.open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != SOURCE_APK_SHA256:
            raise ValueError('Unsupported source APK: use the inspected live79-blaze.apk')
    with zipfile.ZipFile(source) as zin:
        overlays = custom_data_entries(zin, data_dir) if data_dir is not None else {}
        print('Patching native settings and disabling optional remote integrations...', flush=True)
        native = patch_native(zin.read(LIB), server, realtime, voice)
        manifest = patch_manifest(zin.read('AndroidManifest.xml'))
        print('Updating Unity version and Photon assets...', flush=True)
        unity = patch_unity(overlays[DATA].read_bytes() if DATA in overlays else zin.read(DATA),
                            realtime, voice, region)
        policy = {}
        for name in zin.namelist():
            if name.startswith('res/') and name.endswith('.xml'):
                xml = AndroidXml(zin.read(name))
                if 'network-security-config' in xml.strings:
                    policy[name] = patch_network_security(zin.read(name), server.startswith('http://'))
        if len(policy) != 1:
            raise ValueError('Expected exactly one network-security XML resource')
        changes = {name: path.read_bytes() for name, path in overlays.items() if name != DATA}
        changes.update({LIB: native, DATA: unity, 'AndroidManifest.xml': manifest,
                   'resources.arsc': patch_resources(zin.read('resources.arsc')), **policy})
        print('Writing APK with original ZIP compression methods...', flush=True)
        with zipfile.ZipFile(output, 'w') as zout:
            for entry in zin.infolist():
                name = entry.filename
                if name.startswith('META-INF/') and name.rsplit('.', 1)[-1].upper() in {'SF', 'RSA', 'DSA', 'EC', 'MF'}:
                    continue
                zout.writestr(entry, changes[name] if name in changes else zin.read(entry),
                              compress_type=entry.compress_type)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', required=True)
    parser.add_argument('--server', required=True)
    parser.add_argument('--photon-app-id', required=True)
    parser.add_argument('--photon-voice-app-id', required=True)
    parser.add_argument('--photon-region', default='', help='same fixed region for PUN and Voice; blank selects automatically')
    parser.add_argument('--data', type=Path, help='complete edited asset folder from tools/data (bundle and all streamed resources)')
    parser.add_argument('--out', default=f'build/Yeeps-{VERSION}.apk')
    parser.add_argument('--keystore', default=str(Path(KEY_DIR) / 'yeeps-buddy.keystore'))
    args = parser.parse_args()
    server = args.server.rstrip('/')
    validate_settings(server, args.photon_app_id, args.photon_voice_app_id)
    source, output = Path(args.apk), Path(args.out)
    if source.resolve() == output.resolve():
        parser.error('Output must not overwrite the source APK')
    zipalign, apksigner = find_tool('zipalign'), find_tool('apksigner')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Patch and validate before creating a signing identity or final output.
    with tempfile.TemporaryDirectory(dir=output.resolve().parent) as work:
        raw, aligned = Path(work) / 'raw.apk', Path(work) / 'aligned.apk'
        rewrite_apk(source, raw, server, args.photon_app_id, args.photon_voice_app_id, args.photon_region, args.data)
        with zipfile.ZipFile(raw) as z:
            settings = unity_configuration(z.read(DATA))
            custom_hashes = {}
            if args.data is not None:
                with zipfile.ZipFile(source) as original:
                    overlays = custom_data_entries(original, args.data)
                for name, path in overlays.items():
                    with path.open('rb') as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    custom_hashes[path.name] = digest
                    if name != DATA and hashlib.sha256(z.read(name)).hexdigest() != digest:
                        raise ValueError(f'Custom resource round-trip failed: {name}')
        expected = {'version': VERSION, 'realtime': args.photon_app_id,
                    'voice': args.photon_voice_app_id, 'voice_asset': args.photon_voice_app_id,
                    'regions': [args.photon_region, args.photon_region]}
        if settings != expected:
            raise ValueError('Unity configuration round-trip verification failed')
        key = Path(args.keystore)
        password_file = Path(str(key) + '.pass')
        if key.exists() and not password_file.exists():
            raise ValueError(f'Existing keystore needs its password file: {password_file}')
        if not key.exists():
            password = password_file.read_text().strip() if password_file.exists() else secrets.token_urlsafe(24)
            ensure_key(str(key), password)
            password_file.write_text(password, encoding='utf-8')
            password_file.chmod(0o600)
        subprocess.run([zipalign, '-P', '16', '-f', '4', str(raw), str(aligned)], check=True)
        signed = Path(work) / 'signed.apk'
        subprocess.run([apksigner, 'sign', '--ks', str(key), '--ks-pass', f'file:{password_file}',
                        '--out', str(signed), str(aligned)], check=True)
        subprocess.run([apksigner, 'verify', '--verbose', str(signed)], check=True)
        subprocess.run([zipalign, '-c', '-P', '16', '4', str(signed)], check=True)
        # Replace a previous candidate only after every verification succeeds.
        signed.replace(output)
    with output.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    Path(str(output) + '.json').write_text(json.dumps({
        'version': VERSION, 'package': PACKAGE, 'label': APP_LABEL, 'source_sha256': SOURCE_APK_SHA256,
        'sha256': digest, 'server': server, 'photon_region': args.photon_region,
        'static_verified': True, 'headset_verified': False,
        'custom_data_sha256': custom_hashes}, indent=2) + '\n')
    print(f'Verified APK: {output}')
    print(f'SHA256: {digest}')
    print('Ready for the first headset test; gameplay has not yet been verified.')


if __name__ == '__main__':
    main()
