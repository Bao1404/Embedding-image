#!/bin/bash
# Setup VPS dependencies cho price scraping
set -e

echo "=== Installing system dependencies ==="
sudo apt-get update
sudo apt-get install -y xvfb python3 python3-pip python3-venv

echo "=== Installing Python dependencies ==="
pip3 install playwright

echo "=== Installing Chromium browser ==="
python3 -m playwright install chromium
python3 -m playwright install-deps

echo "=== Creating directories ==="
mkdir -p logs

echo "=== Setup complete ==="
