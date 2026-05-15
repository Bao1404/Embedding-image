import urllib.request
import re

url = 'https://scrydex.com/pokemon/expansions'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    html = urllib.request.urlopen(req).read().decode('utf-8')
    urls = set(re.findall(r'href="(/pokemon/expansions/[^"]+)"', html))
    print('--- Expansions ---')
    for u in sorted(urls):
        print(u)
except Exception as e:
    print(f"Error: {e}")
