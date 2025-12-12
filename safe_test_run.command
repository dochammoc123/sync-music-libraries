#!/bin/bash
clear
cd "$(dirname "$0")"
source .venv/bin/activate 
python main.py --mode normal --dry
read -p "Press Enter to close..."