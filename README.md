# Pokémon Card Data Pipeline

This project provides an automated pipeline to extract, process, and manage Pokémon TCG Pocket card data using a scraper.

## Project Structure

- **`app/`**: Core logic for the FastAPI server and Gemini integration.
- **`data/`**: Stores JSON metadata for each expansion. The app reads from this folder.
- **`scraper/`**: Contains Python scripts using Playwright to scrape Scrydex.
  - `scrape_scrydex.py`: Core scraper script. Outputs `{slug}.json` directly to `data/`.
  - `run_missing.py`: Wrapper script to fetch missing expansions. Run this via Cron.
  - `get_expansions.py`: Helper script to get all expansions.
  - `verify.py`: Helper script to verify the downloaded data against the Gemini store.
- **`scripts/`**: System utility scripts.
  - `check_storage.py`: Tool to check Gemini FileSearch Storage limit (1GB).

## Setup & Dependencies

```bash
cd scraper
pip install -r requirements.txt
playwright install chromium
```

## Running the Scraper

To scrape a specific expansion:

```bash
python scraper/scrape_scrydex.py
# (It will prompt for the slug, e.g., fantastical-parade)
```

To run the automated check and scrape missing expansions:

```bash
python scraper/run_missing.py
```

## Setting up Windows Task Scheduler (Cron Job)

To keep the database automatically updated, you can schedule `scraper/run_missing.py` to run daily.

1. Open **Task Scheduler** on Windows.
2. Click **Create Basic Task**.
3. Name it "Update Pokemon Data".
4. Trigger: **Daily**.
5. Action: **Start a program**.
6. Program/script: `python` (or full path to your python executable, e.g., `C:\Python312\python.exe`).
7. Add arguments: `d:\LimGrow\Embedding-image\scraper\run_missing.py`
8. Start in: `d:\LimGrow\Embedding-image`
9. Finish.
