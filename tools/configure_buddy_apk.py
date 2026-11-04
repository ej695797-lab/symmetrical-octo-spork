
#!/usr/bin/env python3
"""Configure, align, sign and verify the Buddy APK."""

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import zipfile

from buddy_apk import (
    AndroidXml,
    SOURCE_APK_SHA256,
    VERSION,
    PACKAGE,
    APP_LABEL,
    patch_manifest,
    patch_resources,
    patch_native,
    patch_network_security,
    patch_unity,
    unity_configuration,
    validate_settings,
)
from configure_apk import find_tool, ensure_key, KEY_DIR

LIB = 'lib/arm64-v8a/libBNMExplorer.so'
DATA = 'assets/bin/Data/data.unity3d'


def custom_data_entries(source_zip: zipfile.ZipFile, data_dir: Path) -> dict:
    """Collect the complete edited Unity asset set."""
    data_dir = Path(data_dir)

    names = [
        entry for entry in source_zip.namelist()
        if entry.startswith('assets/bin/Data/')
        and '/' not in entry[len('assets/bin/Data/'):]
        and (
            entry == DATA
            or entry.endswith(('.resource', '.resS'))
        )
    ]

    if DATA not in names:
        raise ValueError('Source APK has no data.unity3d')

    result = {}

    for name in names:
        path = data_dir / name.rsplit('/', 1)[-1]

        if not path.is_file():
            raise ValueError(
                f'Custom data is incomplete: missing {path.name}'
            )

        result[name] = path

    extras = [
        p.name for p in data_dir.iterdir()
        if p.is_file()
        and p.suffix in {'.resource', '.resS'}
        and 'assets/bin/Data/' + p.name not in result
    ]

    if extras:
        raise ValueError(
            f'Custom data contains unknown resource files: {extras}'
        )

    return result


def rewrite_apk(
    source: Path,
    output: Path,
    server: str,
    realtime: str,
    voice: str,
    region: str = '',
    data_dir: Path | None = None,
    data_only: bool = False,
) -> None:

    if source.resolve() == output.resolve():
        raise ValueError('Output must differ from the source APK')

    if data_dir is None:
        raise ValueError('Provide --data with the edited asset folder')

    with zipfile.ZipFile(source) as zin:
        overlays = custom_data_entries(zin, data_dir)

        # Copy the edited Unity assets into the APK.
        changes = {
            name: path.read_bytes()
            for name, path in overlays.items()
        }

        if not data_only:
            validate_settings(server, realtime, voice)

            print('Patching native settings...', flush=True)

            native = patch_native(
                zin.read(LIB),
                server,
                realtime,
                voice,
            )

            manifest = patch_manifest(
                zin.read('AndroidManifest.xml')
            )

            print('Patching Unity configuration...', flush=True)

            unity = patch_unity(
                overlays[DATA].read_bytes(),
                realtime,
                voice,
                region,
            )

            policy = {}

            for name in zin.namelist():
                if name.startswith('res/') and name.endswith('.xml'):
                    xml = AndroidXml(zin.read(name))

                    if 'network-security-config' in xml.strings:
                        policy[name] = patch_network_security(
                            zin.read(name),
                            server.startswith('http://'),
                        )

            if len(policy) != 1:
                raise ValueError(
                    'Expected exactly one network-security XML resource'
                )

            changes[DATA] = unity
            changes[LIB] = native
            changes['AndroidManifest.xml'] = manifest
            changes['resources.arsc'] = patch_resources(
                zin.read('resources.arsc')
            )
            changes.update(policy)

        print('Writing APK...', flush=True)

        with zipfile.ZipFile(output, 'w') as zout:
            for entry in zin.infolist():
                name = entry.filename

                if (
                    name.startswith('META-INF/')
                    and name.rsplit('.', 1)[-1].upper()
                    in {'SF', 'RSA', 'DSA', 'EC', 'MF'}
                ):
                    continue

                zout.writestr(
                    entry,
                    changes[name] if name in changes else zin.read(entry),
                    compress_type=entry.compress_type,
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument('--apk', required=True)

    parser.add_argument(
        '--allow-unverified-source',
        action='store_true',
        help='Allow an APK that does not match the pinned source hash.',
    )

    parser.add_argument('--server', required=True)
    parser.add_argument('--photon-app-id', required=True)
    parser.add_argument('--photon-voice-app-id', required=True)

    parser.add_argument(
        '--photon-region',
        default='',
        help='Same fixed region for PUN and Voice; blank selects automatically.',
    )

    parser.add_argument(
        '--data',
        type=Path,
        help='Complete edited asset folder from tools/data.',
    )

    parser.add_argument(
        '--data-only',
        action='store_true',
        help='Only replace Unity asset files; leave Photon and native settings unchanged.',
    )

    parser.add_argument(
        '--out',
        default='build/Yeeps-2.32-yeeper.apk',
    )

    parser.add_argument(
        '--keystore',
        default=str(Path(KEY_DIR) / 'yeeps-buddy.keystore'),
    )

    args = parser.parse_args()

    server = args.server.rstrip('/')

    if not args.data_only:
        validate_settings(
            server,
            args.photon_app_id,
            args.photon_voice_app_id,
        )

    source = Path(args.apk)
    output = Path(args.out)

    if not source.is_file():
        parser.error(f'Source APK does not exist: {source}')

    if source.resolve() == output.resolve():
        parser.error('Output must not overwrite the source APK')

    if args.data is None:
        parser.error('You must provide --data')

    # Preserve the source compatibility check.
    with source.open('rb') as f:
        source_hash = hashlib.file_digest(f, 'sha256').hexdigest()

    if (
        source_hash != SOURCE_APK_SHA256
        and not args.allow_unverified_source
    ):
        raise ValueError(
            'Source APK hash does not match the pinned APK. '
            'Check that this is the correct APK, or use '
            '--allow-unverified-source if you have independently '
            'verified its compatibility.'
        )

    zipalign = find_tool('zipalign')
    apksigner = find_tool('apksigner')

    output.parent.mkdir(parents=True, exist_ok=True)

    custom_hashes = {}

    print('Preparing APK build...', flush=True)

    with tempfile.TemporaryDirectory(
        dir=output.resolve().parent
    ) as work:

        raw = Path(work) / 'raw.apk'
        aligned = Path(work) / 'aligned.apk'

        rewrite_apk(
            source,
            raw,
            server,
            args.photon_app_id,
            args.photon_voice_app_id,
            args.photon_region,
            args.data,
            args.data_only,
        )

        print('Verifying copied assets...', flush=True)

        with zipfile.ZipFile(raw) as z:
            with zipfile.ZipFile(source) as original:
                overlays = custom_data_entries(
                    original,
                    args.data,
                )

            for name, path in overlays.items():
                digest = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()

                custom_hashes[path.name] = digest

                # In data-only mode every edited file must match exactly.
                # In normal mode, data.unity3d is additionally patched.
                if args.data_only or name != DATA:
                    actual = hashlib.sha256(
                        z.read(name)
                    ).hexdigest()

                    if actual != digest:
                        raise ValueError(
                            f'Custom data round-trip failed: {name}'
                        )

            if not args.data_only:
                settings = unity_configuration(
                    z.read(DATA)
                )

                expected = {
                    'version': VERSION,
                    'realtime': args.photon_app_id,
                    'voice': args.photon_voice_app_id,
                    'voice_asset': args.photon_voice_app_id,
                    'regions': [
                        args.photon_region,
                        args.photon_region,
                    ],
                }

                if settings != expected:
                    raise ValueError(
                        'Unity configuration round-trip verification failed'
                    )

        print('Preparing signing key...', flush=True)

        key = Path(args.keystore)
        password_file = Path(str(key) + '.pass')

        if key.exists() and not password_file.exists():
            raise ValueError(
                f'Existing keystore needs its password file: {password_file}'
            )

        if not key.exists():
            if password_file.exists():
                password = password_file.read_text().strip()
            else:
                password = secrets.token_urlsafe(24)

            ensure_key(str(key), password)

            password_file.write_text(
                password,
                encoding='utf-8',
            )

            password_file.chmod(0o600)

        print('Aligning APK...', flush=True)

        subprocess.run(
            [
                zipalign,
                '-P',
                '16',
                '-f',
                '4',
                str(raw),
                str(aligned),
            ],
            check=True,
        )

        signed = Path(work) / 'signed.apk'

        print('Signing APK...', flush=True)

        subprocess.run(
            [
                apksigner,
                'sign',
                '--ks',
                str(key),
                '--ks-pass',
                f'file:{password_file}',
                '--out',
                str(signed),
                str(aligned),
            ],
            check=True,
        )

        print('Verifying signature...', flush=True)

        subprocess.run(
            [
                apksigner,
                'verify',
                '--verbose',
                str(signed),
            ],
            check=True,
        )

        subprocess.run(
            [
                zipalign,
                '-c',
                '-P',
                '16',
                '4',
                str(signed),
            ],
            check=True,
        )

        # Only replace the output after successful verification.
        signed.replace(output)

    with output.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()

    metadata = {
        'version': VERSION,
        'package': PACKAGE,
        'label': APP_LABEL,
        'source_sha256': source_hash,
        'sha256': digest,
        'data_only': args.data_only,
        'server': None if args.data_only else server,
        'photon_region': None if args.data_only else args.photon_region,
        'static_verified': True,
        'headset_verified': False,
        'custom_data_sha256': custom_hashes,
    }

    Path(str(output) + '.json').write_text(
        json.dumps(metadata, indent=2) + '\n',
        encoding='utf-8',
    )

    print()
    print('Verified APK:', output)
    print('SHA256:', digest)
    print('Data-only build:', args.data_only)
    print('Ready for the first headset test.')
    

if __name__ == '__main__':
    main()
