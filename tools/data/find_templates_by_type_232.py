
import os
import UnityPy

here = os.path.dirname(os.path.abspath(__file__))
env = UnityPy.load(os.path.join(here, "data", "data.unity3d"))

wanted = {"AudioClip", "AnimationClip", "Animation", "AudioSource"}

for typ in sorted(wanted):
    print(f"\n=== {typ} ===")
    matches = [obj for obj in env.objects if obj.type.name == typ]
    print("Count:", len(matches))

    for obj in matches[:15]:
        print("ID:", obj.path_id, "| File:", obj.assets_file.name)
        try:
            data = obj.read_typetree()
            print("Name:", data.get("m_Name", "(no name)"))
        except Exception as e:
            print("Name unavailable:", str(e))
