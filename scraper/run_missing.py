import subprocess
import time
import os

expansions = [
    "https://scrydex.com/pokemon/expansions/ascended-heroes/me2pt5",
    "https://scrydex.com/pokemon/expansions/nihil-zero/m3_ja",
    "https://scrydex.com/pokemon/expansions/mega-dream-ex/m2a_ja",
    "https://scrydex.com/pokemon/expansions/mega-evolution/me1",
    "https://scrydex.com/pokemon/expansions/mega-evolution-black-star-promos/mep",
    "https://scrydex.com/pokemon/expansions/mega-shine/tcgp-B2b",
    "https://scrydex.com/pokemon/expansions/paldean-wonders/tcgp-B2a",
    "https://scrydex.com/pokemon/expansions/fantastical-parade/tcgp-B2"
]

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"

# Thư mục hiện tại của file chạy
script_dir = os.path.dirname(os.path.abspath(__file__))

for url in expansions:
    print(f"\n{'='*60}")
    print(f"Starting crawl for: {url}")
    print(f"{'='*60}\n")
    
    try:
        # Gọi script qua đường dẫn tương đối so với thư mục gốc
        scraper_path = os.path.join(script_dir, "scrape_scrydex.py")
        subprocess.run(["python", scraper_path, url], check=True, env=env)
        print(f"Successfully finished: {url}")
    except subprocess.CalledProcessError as e:
        print(f"Error crawling {url}: {e}")
    
    # Wait a bit between expansions to avoid rate limiting
    time.sleep(5)

print("\nAll requested crawls completed!")
