import os
import sys
import time
import logging
import subprocess
from datetime import datetime
from threading import Lock

# Import APScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.pool import ThreadPoolExecutor

# Setup Paths
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(APP_DIR)
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Setup Logging for Scheduler
logger = logging.getLogger("scheduler")
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(os.path.join(LOGS_DIR, "scheduler.log"))
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)

# Global lock to ensure only one pipeline runs at a time
pipeline_lock = Lock()

# In-memory APScheduler
scheduler = BackgroundScheduler(
    executors={'default': ThreadPoolExecutor(1)} # Only allow 1 job at a time inside the scheduler thread pool
)

def run_step(script_relative_path: str, args: list = None) -> bool:
    """
    Run a python script in a separate subprocess.
    This guarantees that memory is freed by the OS after the script completes,
    preventing any memory leaks in the long-running FastAPI process.
    """
    if args is None:
        args = []
        
    script_path = os.path.join(PROJECT_DIR, script_relative_path)
    cmd = [sys.executable, script_path] + args
    
    logger.info(f"==> Starting step: {script_relative_path} {args}")
    try:
        # We redirect stdout/stderr to a log file specific to the step
        step_name = os.path.basename(script_relative_path).replace('.py', '')
        log_file_path = os.path.join(LOGS_DIR, f"step_{step_name}.log")
        
        with open(log_file_path, "a") as f:
            f.write(f"\n--- Run at {datetime.now()} ---\n")
            
            # Run the process
            # timeout is 2 hours (7200 seconds) to prevent infinite hangs
            process = subprocess.run(
                cmd,
                cwd=PROJECT_DIR,
                stdout=f,
                stderr=subprocess.STDOUT,
                timeout=7200
            )
            
        if process.returncode != 0:
            logger.error(f"[ERROR] Step {script_relative_path} failed with code {process.returncode}")
            return False
            
        logger.info(f"[DONE] Step {script_relative_path} completed successfully.")
        return True
    
    except subprocess.TimeoutExpired:
        logger.error(f"[TIMEOUT] Step {script_relative_path} exceeded 2 hours limit.")
        return False
    except Exception as e:
        logger.error(f"[FATAL] Failed to execute {script_relative_path}: {e}")
        return False


def run_pipeline(mode="daily"):
    """
    The orchestrator for the pipeline.
    Modes:
      - daily: update_prices -> migrate_qdrant -> enrich_graded
      - weekly: daily + discover -> enrich_tcg -> enrich_cm -> forecast
      - full: full scrape + everything else
    """
    logger.info(f"Attempting to start '{mode}' pipeline...")
    
    # Try to acquire lock, if not skip (prevents overlap if previous job is still running)
    if not pipeline_lock.acquire(blocking=False):
        logger.warning(f"Pipeline is already running! Skipping '{mode}' pipeline.")
        return
        
    try:
        start_time = time.time()
        logger.info(f"=== PIPELINE START ({mode}) ===")
        
        # 1. FULL SCRAPE (Only in Full mode)
        if mode == "full":
            if not run_step("scripts/update_prices.py", ["--full"]): return
            
        # 2. DAILY JOBS (Run in all modes)
        # Update prices
        if not run_step("scripts/update_prices.py", ["--update"]): return
        # Migrate images to Qdrant
        if not run_step("scripts/migrate_to_qdrant.py"): return
        # Enrich Graded Prices
        if not run_step("scripts/enrich_graded_prices.py"): return
        
        # 3. WEEKLY JOBS (Run in Weekly and Full modes)
        if mode in ["weekly", "full"]:
            # Discover new expansions
            if not run_step("scripts/update_prices.py", ["--discover"]): return
            # Enrich TCGPlayer
            if not run_step("scripts/enrich_tcgplayer.py"): return
            # Enrich Cardmarket
            if not run_step("scripts/enrich_cardmarket.py"): return
            # Generate Forecasts
            if not run_step("scripts/generate_forecasts.py"): return
            
        elapsed = time.time() - start_time
        logger.info(f"=== PIPELINE COMPLETE ({mode}) in {elapsed:.2f}s ===")
        
    finally:
        pipeline_lock.release()


def start_scheduler():
    """Start the APScheduler with configured jobs."""
    logger.info("Initializing background scheduler...")
    
    # Run Daily Pipeline at 03:00 AM every day
    scheduler.add_job(
        run_pipeline,
        CronTrigger(hour=3, minute=0),
        args=["daily"],
        id="pipeline_daily",
        replace_existing=True
    )
    
    # Run Weekly Pipeline at 02:00 AM every Monday
    scheduler.add_job(
        run_pipeline,
        CronTrigger(day_of_week='mon', hour=2, minute=0),
        args=["weekly"],
        id="pipeline_weekly",
        replace_existing=True
    )
    
    # Run Full Pipeline at 01:00 AM every Sunday
    scheduler.add_job(
        run_pipeline,
        CronTrigger(day_of_week='sun', hour=1, minute=0),
        args=["full"],
        id="pipeline_full",
        replace_existing=True
    )
    
    scheduler.start()
    logger.info("Scheduler started successfully. Scheduled jobs:")
    for job in scheduler.get_jobs():
        logger.info(f" - {job.id}: {job.trigger}")


def stop_scheduler():
    """Shutdown the scheduler gracefully."""
    logger.info("Shutting down background scheduler...")
    if scheduler.running:
        scheduler.shutdown(wait=False)
