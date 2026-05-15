import json, glob, os

script_dir = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(script_dir, "..", "data")

f = sorted(glob.glob(os.path.join(data_dir, '*.json')))[-1]
data = json.load(open(f, 'r', encoding='utf-8'))
c = data['cards'][0]
print('File:', f)
print('Name:', c.get('name'))
print('Attacks:')
for a in c.get('attacks', []):
    print(f"  - {a['name']} | dmg={a['damage']} | {a['text'][:80]}")
print('Artist:', c.get('artist'))
print('Rarity:', c.get('rarity'))
