#!/bin/bash
clear
cd "$(dirname "$0")"
"/Users/christopherhammons/Library/Mobile Documents/com~apple~CloudDocs/scripts/.venv/bin/python" \
	main.py --mode embed
read -p "Press Enter to close..."