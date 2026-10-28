#!/usr/bin/env python3
"""Add a separate named record to TEST COPIES of Buddy 2.32 assets.

Unlike bake_disk.py, this appends a new audio ID and record ID, preserving every
existing song. Inputs must be local audio files; downloading is a separate step.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

import imageio_ffmpeg
from PIL import Image
import UnityPy
from UnityPy.enums import TextureFormat

from buddy_content import (append_audio, append_media, append_pattern, atomic_write,
                           clone, manager, parse_audio, parse_media, pptr, resolve)

RATE = 44100


def fsb_pcm(pcm):
    if not pcm or len(pcm)%2: raise ValueError('Expected nonempty mono PCM16')
    samples=len(pcm)//2
    if samples >= 1<<30: raise ValueError('Audio exceeds FSB5 sample limit')
    header=(8<<1) | (samples<<34)
    return (b'FSB5'+struct.pack('<6I',1,1,36,0,len(pcm),2)+bytes(32)
            +struct.pack('<Q',header)+bytes(28)+pcm)


def list_records(src):
    env=UnityPy.load(str(Path(src)/'data.unity3d'))
    media,_=parse_media(manager(env,'PlayableMediaItemManager').get_raw_data())
    audio,_=parse_audio(manager(env,'RoutableAudioManager').get_raw_data())
    for i,m in enumerate(media):
        a=audio[m['audio_id']] if 0<=m['audio_id']<len(audio) else {}
        print(f"{i}: {m['key']} | {a.get('title','')} | {a.get('key','unavailable')}")


def add_disc(src, dst, audio_path, key, name, artist='', label=None, cost=0):
    src,dst=Path(src),Path(dst)
    if src.resolve()==dst.resolve() or dst.exists():
        raise ValueError('Output must be a new directory; source data stays unchanged')
    if not re.fullmatch(r'record_[A-Za-z0-9_]+',key):
        raise ValueError('Record key must start with record_ and use letters/digits/underscores')
    if not Path(audio_path).is_file(): raise ValueError('Audio must be a local file')
    env=UnityPy.load(str(src/'data.unity3d'))
    patterns=manager(env,'PatternManager'); audio=manager(env,'RoutableAudioManager')
    media=manager(env,'PlayableMediaItemManager')
    records,_=parse_media(media.get_raw_data()); songs,_=parse_audio(audio.get_raw_data())
    if any(d['key']==key for d in records): raise ValueError(f'Record {key!r} already exists')
    template=next(d for d in records if d['key']=='record_suburbTheme')
    artwork=resolve(media,pptr(template['art'][1],template['art'][0]))
    if artwork.type.name!='Texture2D': raise ValueError('Unexpected record artwork reference')
    art_id=max(artwork.assets_file.objects)+1
    tex=clone(artwork,art_id).read(); tex.m_Name=key+'_label'
    # Each record owns an artwork asset, while retaining the game's atlas style.
    if label:
        image=artwork.read().image.convert('RGBA')
        q=image.width//2
        with Image.open(label) as pic:
            image.paste(pic.convert('RGBA').resize((q,q),Image.Resampling.LANCZOS),(0,0))
        tex.set_image(image,target_format=TextureFormat.RGBA32,mipmap_count=1)
    tex.save()
    source_song=songs[template['audio_id']]
    source_clip=resolve(audio,pptr(source_song['clip'][1],source_song['clip'][0]))
    if source_clip.type.name!='AudioClip': raise ValueError('Unexpected audio reference')
    clip_id=max(source_clip.assets_file.objects)+1
    pcm=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error',
                        '-i',str(audio_path),'-vn','-ac','1','-ar',str(RATE),
                        '-af','loudnorm=I=-14:TP=-1.5:LRA=11','-f','s16le','-'],
                       capture_output=True,check=True).stdout
    fsb=fsb_pcm(pcm); duration=len(pcm)/2/RATE
    clip=source_clip.read_typetree(); resource=clip['m_Resource']['m_Source']
    resource_name=resource.replace('\\','/').rsplit('/',1)[-1]
    if not resource_name or not (src/resource_name).is_file():
        raise ValueError(f'Missing streamed audio resource {resource_name!r}; extract all resources first')
    size=(src/resource_name).stat().st_size; pad=-size%32; offset=size+pad
    song_key='song_'+key[len('record_'):]
    audio_raw,audio_id=append_audio(audio.get_raw_data(),song_key,name,artist,source_song['clip'][0],clip_id,duration)
    media_raw,media_id=append_media(media.get_raw_data(),key,audio_id,template['art'][0],art_id)
    pattern_raw=append_pattern(patterns.get_raw_data(),'records',key,name,'record',[str(media_id)],'',cost)
    clip.update(m_Name=song_key,m_Channels=1,m_Frequency=RATE,m_BitsPerSample=16,
                m_Length=float(duration),m_LoadInBackground=True,m_CompressionFormat=0)
    clip['m_Resource'].update(m_Offset=offset,m_Size=len(fsb))
    clone(source_clip,clip_id).save_typetree(clip)
    audio.set_raw_data(audio_raw); media.set_raw_data(media_raw); patterns.set_raw_data(pattern_raw)
    bundle=env.file.save(packer='lz4')
    result=dict(key=key,displayName=name,artist=artist,recordID=media_id,audioID=audio_id,
                clipFile=source_clip.assets_file.name,clipPathID=clip_id,artFile=artwork.assets_file.name,artPathID=art_id,
                resource=resource_name,offset=offset,bytes=len(fsb),duration=duration,headsetVerified=False)
    # Stage the entire output folder; conversion/validation failures leave no output.
    dst.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dst.parent) as work:
        stage=Path(work)/'data';stage.mkdir()
        for path in src.iterdir():
            if path.is_file() and path.suffix in ['.resource','.resS']:
                shutil.copy2(path,stage/path.name)
        with (stage/resource_name).open('ab') as stream:
            stream.write(bytes(pad));stream.write(fsb)
        atomic_write(stage/'data.unity3d',bundle)
        atomic_write(stage/'custom-disc.json',(json.dumps(result,indent=2)+'\n').encode())
        stage.rename(dst)
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('src',help='extracted game data folder')
    ap.add_argument('dst',nargs='?',help='new output folder')
    ap.add_argument('--list',action='store_true')
    ap.add_argument('--audio');ap.add_argument('--key');ap.add_argument('--name')
    ap.add_argument('--artist',default='');ap.add_argument('--label');ap.add_argument('--cost',type=int,default=0)
    a=ap.parse_args()
    if a.list: list_records(a.src);return
    if not all([a.dst,a.audio,a.key,a.name]): ap.error('dst, --audio, --key and --name are required')
    try: print(json.dumps(add_disc(a.src,a.dst,a.audio,a.key,a.name,a.artist,a.label,a.cost),indent=2))
    except (ValueError,OSError,subprocess.CalledProcessError) as exc: ap.exit(1,f'{exc}\n')


if __name__=='__main__': main()
