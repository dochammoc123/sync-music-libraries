#!/bin/bash
# Reset test environment for manual testing
# This cleans up test directories so you can start fresh

set -e

DOWNLOADS_MUSIC="$HOME/Downloads/Music"
ICLOUD_SCRIPTS="$HOME/Library/Mobile Documents/com~apple~CloudDocs/scripts"
TEST_MUSIC_ROOT="$ICLOUD_SCRIPTS/TestMusicLibrary/ROON/Music"
TEST_T8_ROOT="$ICLOUD_SCRIPTS/TestMusicLibrary/T8/Music"
BACKUP_ROOT="$ICLOUD_SCRIPTS/TestMusicLibrary/_EmbeddedArtOriginal"
UPDATE_ROOT="$ICLOUD_SCRIPTS/TestMusicLibrary/_UpdateOverlay"

echo "========================================"
echo "Reset Test Environment"
echo "========================================"
echo ""
echo "This will clean up test directories for fresh testing."
echo ""
echo "WARNING: This will delete:"
echo "  - All files in $DOWNLOADS_MUSIC"
echo "  - All files in $TEST_MUSIC_ROOT"
echo "  - All files in $TEST_T8_ROOT"
echo "  - All files in $BACKUP_ROOT"
echo "  - All files in $UPDATE_ROOT"
echo ""
read -p "Are you sure? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo "Cleaning up..."

# Clean downloads (but keep the directory)
if [ -d "$DOWNLOADS_MUSIC" ]; then
    echo "Cleaning Downloads/Music..."
    find "$DOWNLOADS_MUSIC" -mindepth 1 -delete 2>/dev/null || true
fi

# Clean test music library
if [ -d "$TEST_MUSIC_ROOT" ]; then
    echo "Cleaning ROON/Music..."
    find "$TEST_MUSIC_ROOT" -mindepth 1 -delete 2>/dev/null || true
fi

# Clean T8 library
if [ -d "$TEST_T8_ROOT" ]; then
    echo "Cleaning T8/Music..."
    find "$TEST_T8_ROOT" -mindepth 1 -delete 2>/dev/null || true
fi

# Clean backup directory
if [ -d "$BACKUP_ROOT" ]; then
    echo "Cleaning backup directory..."
    find "$BACKUP_ROOT" -mindepth 1 -delete 2>/dev/null || true
fi

# Clean update overlay
if [ -d "$UPDATE_ROOT" ]; then
    echo "Cleaning update overlay..."
    find "$UPDATE_ROOT" -mindepth 1 -delete 2>/dev/null || true
fi

echo ""
echo "========================================"
echo "Reset Complete!"
echo "========================================"
echo ""
echo "Test environment cleaned. Ready for fresh testing."
echo ""
echo "Next steps:"
echo "  1. Copy a few albums from your iTunes library to $DOWNLOADS_MUSIC"
echo "  2. Run the sync script to test"
echo ""
read -p "Press Enter to continue..."

