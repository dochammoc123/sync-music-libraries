#!/bin/bash
# Optional: Create test directories if they don't exist
# This ensures all test paths are ready

set -e

DOWNLOADS_MUSIC="$HOME/Downloads/Music"
ICLOUD_SCRIPTS="$HOME/Library/Mobile Documents/com~apple~CloudDocs/scripts"
TEST_MUSIC_ROOT="$ICLOUD_SCRIPTS/TestMusicLibrary/ROON/Music"
TEST_T8_ROOT="$ICLOUD_SCRIPTS/TestMusicLibrary/T8/Music"
BACKUP_ROOT="$ICLOUD_SCRIPTS/TestMusicLibrary/_EmbeddedArtOriginal"
UPDATE_ROOT="$ICLOUD_SCRIPTS/TestMusicLibrary/_UpdateOverlay"

echo "========================================"
echo "Setting Up Test Directories"
echo "========================================"
echo ""

# Create directories if they don't exist
if [ ! -d "$DOWNLOADS_MUSIC" ]; then
    echo "Creating $DOWNLOADS_MUSIC..."
    mkdir -p "$DOWNLOADS_MUSIC"
fi

if [ ! -d "$TEST_MUSIC_ROOT" ]; then
    echo "Creating $TEST_MUSIC_ROOT..."
    mkdir -p "$TEST_MUSIC_ROOT"
fi

if [ ! -d "$TEST_T8_ROOT" ]; then
    echo "Creating $TEST_T8_ROOT..."
    mkdir -p "$TEST_T8_ROOT"
fi

if [ ! -d "$BACKUP_ROOT" ]; then
    echo "Creating $BACKUP_ROOT..."
    mkdir -p "$BACKUP_ROOT"
fi

if [ ! -d "$UPDATE_ROOT" ]; then
    echo "Creating $UPDATE_ROOT..."
    mkdir -p "$UPDATE_ROOT"
fi

echo ""
echo "========================================"
echo "Setup Complete!"
echo "========================================"
echo ""
echo "All test directories are ready."
echo ""
echo "Test workflow:"
echo "  1. Copy albums from your iTunes library to $DOWNLOADS_MUSIC"
echo "  2. Run: cd \"$ICLOUD_SCRIPTS/music-sync-refactored\""
echo "  3. Run: python3 main.py --mode normal --dry"
echo "  4. Review output, then run without --dry"
echo ""
read -p "Press Enter to continue..."

