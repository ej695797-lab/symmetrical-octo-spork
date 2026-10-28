#!/usr/bin/env python3
"""Add a named static GLB decoration to a TEST COPY of Buddy 2.32 game data."""
import argparse
import copy
import json
from pathlib import Path
import re
import struct

import UnityPy
from UnityPy.enums import TextureFormat

from buddy_content import (append_pattern, atomic_write, clone, manager, pptr,
                           resolve, script_name)
from glb_model import load_glb

TEMPLATE = 'mapobjects/complexdecorationstuffed/1x1_stuffedcucumbercat'
INTERACTION_TEMPLATE = 'mapobjects/stuffed/1x1_stuffed'


def mesh_tree(template, name, positions, normals, uv, indices):
    t = copy.deepcopy(template)
    t['m_Name'] = name
    vertices = b''.join(struct.pack('<3f3f4f2f', *p, *n, 1, 0, 0, -1, *u)
                        for p,n,u in zip(positions,normals,uv))
    t['m_VertexData'] = {'m_VertexCount': len(positions),
                           'm_Channels': [dict(stream=0,offset=offset,format=0,dimension=dim)
                                          for offset,dim in [(0,3),(12,3),(24,4),(0,0),(40,2)]
                                          + [(0,0)]*9], 'm_DataSize': vertices}
    t['m_IndexFormat'] = int(len(positions)>65535)
    ib = struct.pack('<'+('I' if t['m_IndexFormat'] else 'H')*len(indices), *indices)
    t['m_IndexBuffer'] = list(ib) if isinstance(t['m_IndexBuffer'],list) else ib
    extent = {axis:max(abs(p[i]) for p in positions) for i,axis in enumerate('xyz')}
    aabb = {'m_Center': dict(x=0.,y=0.,z=0.), 'm_Extent': extent}
    sub = t['m_SubMeshes'][0]
    t['m_SubMeshes'] = [dict(sub,firstByte=0,indexCount=len(indices),baseVertex=0,
                           firstVertex=0,vertexCount=len(positions),localAABB=aabb)]
    t['m_LocalAABB'] = aabb
    t['m_StreamData'] = dict(offset=0,size=0,path='')
    t['m_BakedConvexCollisionMesh'] = []
    t['m_BakedTriangleCollisionMesh'] = []
    return t


def configure_material(template, gltf_material):
    mode = gltf_material.get('alphaMode', 'OPAQUE')
    if mode not in ['OPAQUE', 'MASK']:
        raise ValueError(f'Unsupported alpha mode {mode}; BLEND is not supported')
    t = copy.deepcopy(template)
    cutoff = float(gltf_material.get('alphaCutoff', .5))
    if not 0 <= cutoff <= 1:
        raise ValueError('Alpha cutoff must be between zero and one')
    props = dict(t['m_SavedProperties']['m_Floats'])
    props.update(_Cull=0. if gltf_material.get('doubleSided', False) else 2.,
                 _AlphaClip=float(mode == 'MASK'), _Cutoff=cutoff,
                 _AlphaClipThreshold=cutoff)
    t['m_SavedProperties']['m_Floats'] = list(props.items())
    t['m_ValidKeywords'] = [k for k in t['m_ValidKeywords'] if k != '_ALPHATEST_ON']
    t['m_InvalidKeywords'] = [k for k in t['m_InvalidKeywords'] if k != '_ALPHATEST_ON']
    if mode == 'MASK':
        t['m_ValidKeywords'].append('_ALPHATEST_ON')
    tags = dict(t['stringTagMap'])
    tags['RenderType'] = 'TransparentCutout' if mode == 'MASK' else 'Opaque'
    t['stringTagMap'] = list(tags.items())
    t['m_CustomRenderQueue'] = 2450 if mode == 'MASK' else -1
    return t


def add_model(src, dst, model_path, key, name, size=(1,1,1), cost=0):
    if Path(src).resolve()==Path(dst).resolve():
        raise ValueError('Use a different output path; preserve the input test data')
    if not re.fullmatch(r'[A-Za-z0-9_]+',key):
        raise ValueError('Key must contain only letters, digits and underscores')
    model = load_glb(model_path, size)
    env = UnityPy.load(str(src))
    patterns = manager(env, 'PatternManager')
    pattern_raw = append_pattern(patterns.get_raw_data(),'stuffed',key,name,'mapObject',[],'white',cost)
    rm = next(o for o in env.objects if o.type.name=='ResourceManager')
    rt = rm.read_typetree(); resources = dict(rt['m_Container'])
    path = 'mapobjects/complexdecorationstuffed/'+key.lower()
    if path in resources: raise ValueError(f'Resource {path!r} already exists')
    go = resolve(rm, resources[TEMPLATE]); f=go.assets_file
    comps = {o.type.name:o for c in go.read_typetree()['m_Component']
             for o in [resolve(go,c['component'])]}
    mb = comps['MonoBehaviour']
    if script_name(mb)!='MapObject': raise ValueError('Template is not a plain MapObject')
    mesh = resolve(comps['MeshFilter'],comps['MeshFilter'].read_typetree()['m_Mesh'])
    renderer = comps['MeshRenderer']; renderer_tree=renderer.read_typetree()
    material = resolve(renderer,renderer_tree['m_Materials'][1])
    mt=material.read_typetree()
    texref=next(v['m_Texture'] for k,v in mt['m_SavedProperties']['m_TexEnvs'] if k=='_BaseMap')
    texture=resolve(material,texref)
    labels=['go','tr','mb','mf','mr','bc','hidden','child_go','child_tr','child_mf','child_mr','mesh','mat','tex']
    ids={k:max(f.objects)+1+i for i,k in enumerate(labels)}
    root=go.read_typetree(); root['m_Name']=key
    # Cucumber Cat is untagged. The game's block-selection/pickup path needs
    # the same Destructable tag on the collider owner as an ordinary pillow.
    pillow=resolve(rm,resources[INTERACTION_TEMPLATE]).read_typetree()
    root['m_Tag']=pillow['m_Tag']
    root['m_Layer']=pillow['m_Layer']
    root['m_Component']=[{'component':pptr(ids[k])} for k in ['tr','mb','mf','mr','bc']]
    clone(go,ids['go']).save_typetree(root)
    tr=comps['Transform'].read_typetree(); tr['m_GameObject']=pptr(ids['go'])
    tr['m_Children']=[pptr(ids['child_tr'])]; tr['m_Father']=pptr(0)
    clone(comps['Transform'],ids['tr']).save_typetree(tr)
    # Preserve the modern MapObject fields, changing only its owner and grid size.
    raw=bytearray(mb.get_raw_data())
    if len(raw)!=144 or struct.unpack_from('<i',raw,28)[0]!=0:
        raise ValueError('Unexpected modern MapObject template layout')
    struct.pack_into('<iq',raw,0,0,ids['go']); struct.pack_into('<3i',raw,32,*size)
    clone(mb,ids['mb']).set_raw_data(bytes(raw))
    mf=comps['MeshFilter'].read_typetree(); mf['m_GameObject']=pptr(ids['go']); mf['m_Mesh']=pptr(ids['hidden'])
    clone(comps['MeshFilter'],ids['mf']).save_typetree(mf)
    rr=copy.deepcopy(renderer_tree); rr['m_GameObject']=pptr(ids['go']); rr['m_Materials']=rr['m_Materials'][:1]
    clone(renderer,ids['mr']).save_typetree(rr)
    box=comps['BoxCollider'].read_typetree(); box['m_GameObject']=pptr(ids['go'])
    box['m_Size']={a:.5*size[i] for i,a in enumerate('xyz')}
    clone(comps['BoxCollider'],ids['bc']).save_typetree(box)
    clone(mesh,ids['hidden']).save_typetree(mesh_tree(mesh.read_typetree(),key+'_paint',[(0.,0.,0.)]*3,[(0.,1.,0.)]*3,[(0.,0.)]*3,[0,1,2]))
    child=copy.deepcopy(root); child['m_Name']=key+'_model'
    child['m_Component']=[{'component':pptr(ids[k])} for k in ['child_tr','child_mf','child_mr']]
    clone(go,ids['child_go']).save_typetree(child)
    tr['m_GameObject']=pptr(ids['child_go']); tr['m_Children']=[]; tr['m_Father']=pptr(ids['tr'])
    clone(comps['Transform'],ids['child_tr']).save_typetree(tr)
    mf['m_GameObject']=pptr(ids['child_go']); mf['m_Mesh']=pptr(ids['mesh'])
    clone(comps['MeshFilter'],ids['child_mf']).save_typetree(mf)
    rr['m_GameObject']=pptr(ids['child_go']); rr['m_Materials']=[pptr(ids['mat'])]
    clone(renderer,ids['child_mr']).save_typetree(rr)
    clone(mesh,ids['mesh']).save_typetree(mesh_tree(mesh.read_typetree(),key,model['positions'],model['normals'],model['uv'],model['indices']))
    tex=clone(texture,ids['tex']).read(); tex.m_Name=key
    tex.set_image(model['image'],target_format=TextureFormat.RGBA32,mipmap_count=1)
    sampler=model['sampler']
    tex.m_TextureSettings.m_FilterMode=0 if sampler.get('magFilter',9729)==9728 else 1
    wrap_modes={10497:0,33071:1,33648:2}
    tex.m_TextureSettings.m_WrapU=wrap_modes[sampler.get('wrapS',10497)]
    tex.m_TextureSettings.m_WrapV=wrap_modes[sampler.get('wrapT',10497)]
    tex.save()
    mt['m_Name']=key
    mt['m_SavedProperties']['m_TexEnvs']=[(k,dict(v,m_Texture=pptr(ids['tex']))) for k,v in mt['m_SavedProperties']['m_TexEnvs']]
    mt=configure_material(mt,model['material'])
    clone(material,ids['mat']).save_typetree(mt)
    rt['m_Container'].append((path,pptr(ids['go'],resources[TEMPLATE]['m_FileID'])))
    rm.save_typetree(rt); patterns.set_raw_data(pattern_raw)
    atomic_write(dst,env.file.save(packer='lz4'))
    result=dict(key=key,displayName=name,resource=path,gridSize=list(size),
                vertices=len(model['positions']),triangles=len(model['indices'])//3,
                attribution=model['attribution'],headsetVerified=False)
    atomic_write(str(dst)+'.block.json',(json.dumps(result,indent=2)+'\n').encode())
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('src'); ap.add_argument('dst')
    ap.add_argument('--model',required=True); ap.add_argument('--key',required=True)
    ap.add_argument('--name',required=True); ap.add_argument('--size',type=int,nargs=3,default=[1,1,1],metavar=('X','Y','Z'))
    ap.add_argument('--cost',type=int,default=0)
    a=ap.parse_args()
    try: print(json.dumps(add_model(a.src,a.dst,a.model,a.key,a.name,a.size,a.cost),indent=2))
    except (ValueError,OSError) as exc: ap.exit(1,f'{exc}\n')


if __name__=='__main__': main()
