#!/bin/bash
clear
cd "$(dirname "$0")"
source .venv/bin/activate 
python library_sync_and_upgrade.py --mode normal --dry
read -p "Press Enter to close..."