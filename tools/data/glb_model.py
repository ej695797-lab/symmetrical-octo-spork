"""Small static, single-texture GLB 2 importer; no Blender/Unity Editor required."""
import io
import json
import math
from pathlib import Path
import struct

from PIL import Image

IDENTITY = [[float(i == j) for j in range(4)] for i in range(4)]


def multiply(a, b):
    return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def transform(node):
    if 'matrix' in node:
        m = node['matrix']
        return [[m[4*j+i] for j in range(4)] for i in range(4)]
    x,y,z,w = node.get('rotation', [0,0,0,1])
    sx,sy,sz = node.get('scale', [1,1,1])
    tx,ty,tz = node.get('translation', [0,0,0])
    return [[(1-2*y*y-2*z*z)*sx, (2*x*y-2*z*w)*sy, (2*x*z+2*y*w)*sz, tx],
            [(2*x*y+2*z*w)*sx, (1-2*x*x-2*z*z)*sy, (2*y*z-2*x*w)*sz, ty],
            [(2*x*z-2*y*w)*sx, (2*y*z+2*x*w)*sy, (1-2*x*x-2*y*y)*sz, tz],
            [0,0,0,1]]


def cross(a,b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def load_glb(path, size=(1,1,1)):
    try:
        return _load(path, size)
    except (IndexError, KeyError, struct.error, TypeError, ZeroDivisionError) as exc:
        raise ValueError(f'Unsupported or malformed GLB: {exc}') from exc


def _load(path, size):
    b = Path(path).read_bytes()
    if len(b)<20 or struct.unpack_from('<III', b) != (0x46546c67, 2, len(b)):
        raise ValueError('Expected a complete GLB 2 file')
    chunks, off = {}, 12
    while off < len(b):
        n, kind = struct.unpack_from('<II', b, off); off += 8
        if off+n > len(b) or n%4 or kind in chunks:
            raise ValueError('Invalid GLB chunk')
        chunks[kind] = b[off:off+n]; off += n
    d = json.loads(chunks[0x4e4f534a]); blob = chunks[0x004e4942]
    if d.get('extensionsRequired') or d.get('animations') or d.get('skins'):
        raise ValueError('Only static GLB without required extensions is supported')
    if len(d['buffers'])!=1 or d['buffers'][0].get('uri'):
        raise ValueError('Expected one embedded GLB buffer')

    def view(index):
        v = d['bufferViews'][index]; start = v.get('byteOffset',0); n=v['byteLength']
        if v.get('buffer',0)!=0 or start<0 or n<0 or start+n>len(blob):
            raise ValueError('Buffer view outside embedded buffer')
        return blob[start:start+n], v

    def accessor(index):
        a=d['accessors'][index]
        if a.get('sparse') or a.get('normalized'):
            raise ValueError('Sparse/normalized accessors are not supported')
        fmt={5121:'B',5123:'H',5125:'I',5126:'f'}[a['componentType']]
        dim={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}[a['type']]
        data,v=view(a['bufferView']); width=struct.calcsize(fmt)*dim
        stride=v.get('byteStride',width); offset=a.get('byteOffset',0); count=a['count']
        if count<0 or offset<0 or stride<width or (count and offset+(count-1)*stride+width>len(data)):
            raise ValueError('Accessor outside buffer view')
        return [struct.unpack_from('<'+fmt*dim,data,offset+i*stride) for i in range(count)]

    positions,normals,uvs,indices=[],[],[],[]; used_materials=set()
    def walk(index, parent, ancestors):
        if index in ancestors: raise ValueError('Cyclic GLB hierarchy')
        node=d['nodes'][index]; m=multiply(parent, transform(node))
        # Cofactor matrix transforms normals by inverse transpose, including nonuniform scaling.
        a,bv,c=[tuple(m[i][j] for i in range(3)) for j in range(3)]
        co=[cross(bv,c),cross(c,a),cross(a,bv)]
        det=sum(a[i]*co[0][i] for i in range(3))
        if abs(det)<1e-12: raise ValueError('Singular node transform')
        if 'mesh' in node:
            for prim in d['meshes'][node['mesh']]['primitives']:
                if prim.get('mode',4)!=4 or prim.get('targets'):
                    raise ValueError('Only static triangle meshes are supported')
                attr=prim['attributes']; ps=accessor(attr['POSITION']); ns=accessor(attr['NORMAL']); us=accessor(attr['TEXCOORD_0'])
                if not (len(ps)==len(ns)==len(us)): raise ValueError('Mismatched mesh attributes')
                used_materials.add(prim['material']); base=len(positions)
                for p,n,u in zip(ps,ns,us):
                    q=[sum(m[i][j]*p[j] for j in range(3))+m[i][3] for i in range(3)]
                    positions.append((q[0],q[1],-q[2]))
                    nn=[sum(co[j][i]*n[j] for j in range(3))/det for i in range(3)]
                    length=math.sqrt(sum(x*x for x in nn))
                    normals.append((nn[0]/length,nn[1]/length,-nn[2]/length))
                    uvs.append((u[0],1-u[1]))
                ix=[v[0] for v in accessor(prim['indices'])] if 'indices' in prim else list(range(len(ps)))
                if len(ix)%3 or any(i<0 or i>=len(ps) for i in ix): raise ValueError('Invalid triangle indices')
                for j in range(0,len(ix),3):
                    tri=ix[j:j+3]
                    if det>0: tri=[tri[0],tri[2],tri[1]]
                    indices.extend(base+i for i in tri)
        for child in node.get('children',[]): walk(child,m,ancestors|{index})
    for node in d['scenes'][d.get('scene',0)]['nodes']: walk(node,IDENTITY,set())
    if not positions or not indices or len(used_materials)!=1:
        raise ValueError('Expected a nonempty mesh using one textured material')
    if not all(math.isfinite(v) for rows in [positions,normals,uvs] for row in rows for v in row):
        raise ValueError('Nonfinite model coordinates')
    if len(size)!=3 or any(n<=0 for n in size): raise ValueError('Grid dimensions must be positive')
    lo=[min(p[i] for p in positions) for i in range(3)]; hi=[max(p[i] for p in positions) for i in range(3)]
    scale=min(.5*size[i]/(hi[i]-lo[i]) for i in range(3) if hi[i]>lo[i])
    positions=[tuple((p[i]-(lo[i]+hi[i])/2)*scale for i in range(3)) for p in positions]
    mat=d['materials'][next(iter(used_materials))]
    pbr=mat['pbrMetallicRoughness']; texinfo=pbr['baseColorTexture']
    if texinfo.get('texCoord',0)!=0 or texinfo.get('extensions') or pbr.get('baseColorFactor',[1,1,1,1]) != [1,1,1,1]:
        raise ValueError('Only unmodified base-color texture UV0 is supported')
    texture=d['textures'][texinfo['index']]
    sampler=d.get('samplers',[])[texture['sampler']] if 'sampler' in texture else {}
    im=d['images'][texture['source']]
    image=Image.open(io.BytesIO(view(im['bufferView'])[0])).convert('RGBA')
    return dict(positions=positions,normals=normals,uv=uvs,indices=indices,image=image,
                material=mat,sampler=sampler,attribution=d.get('asset',{}).get('extras',{}))
