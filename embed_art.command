#!/bin/bash
clear
cd "$(dirname "$0")"
"/Users/christopherhammons/Library/Mobile Documents/com~apple~CloudDocs/scripts/.venv/bin/python" \
	library_sync_and_upgrade.py --mode embed
read -p "Press Enter to close..."