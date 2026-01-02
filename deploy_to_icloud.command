#!/bin/bash
# Deploy refactored music library sync to iCloud scripts folder for testing
# This copies the new modular code to a subfolder for testing

set -e

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
ICLOUD_SCRIPTS="$HOME/Library/Mobile Documents/com~apple~CloudDocs/scripts"
DEPLOY_FOLDER="$ICLOUD_SCRIPTS/music-sync-refactored"

echo "========================================"
echo "Deploying Refactored Music Library Sync"
echo "========================================"
echo ""
echo "Source: $SOURCE_DIR"
echo "Target: $DEPLOY_FOLDER"
echo ""

# Create target directory
if [ ! -d "$DEPLOY_FOLDER" ]; then
    echo "Creating deployment folder..."
    mkdir -p "$DEPLOY_FOLDER"
fi

# Copy Python modules
echo "Copying Python modules..."
cp -f "$SOURCE_DIR/main.py" "$DEPLOY_FOLDER/main.py"
cp -f "$SOURCE_DIR/config.py" "$DEPLOY_FOLDER/config.py"
cp -f "$SOURCE_DIR/logging_utils.py" "$DEPLOY_FOLDER/logging_utils.py"
cp -f "$SOURCE_DIR/structured_logging.py" "$DEPLOY_FOLDER/structured_logging.py"
cp -f "$SOURCE_DIR/tag_operations.py" "$DEPLOY_FOLDER/tag_operations.py"
cp -f "$SOURCE_DIR/artwork.py" "$DEPLOY_FOLDER/artwork.py"
cp -f "$SOURCE_DIR/file_operations.py" "$DEPLOY_FOLDER/file_operations.py"
cp -f "$SOURCE_DIR/sync_operations.py" "$DEPLOY_FOLDER/sync_operations.py"
cp -f "$SOURCE_DIR/roon_refresh.py" "$DEPLOY_FOLDER/roon_refresh.py"

# Copy test script
if [ -f "$SOURCE_DIR/test_quick.py" ]; then
    cp -f "$SOURCE_DIR/test_quick.py" "$DEPLOY_FOLDER/test_quick.py"
fi

# Copy existing run scripts
echo "Copying run scripts..."
if [ -f "$SOURCE_DIR/normal_run.command" ]; then
    cp -f "$SOURCE_DIR/normal_run.command" "$DEPLOY_FOLDER/normal_run.command"
    chmod +x "$DEPLOY_FOLDER/normal_run.command"
fi
if [ -f "$SOURCE_DIR/restore_originals.command" ]; then
    cp -f "$SOURCE_DIR/restore_originals.command" "$DEPLOY_FOLDER/restore_originals.command"
    chmod +x "$DEPLOY_FOLDER/restore_originals.command"
fi
if [ -f "$SOURCE_DIR/safe_test_run.command" ]; then
    cp -f "$SOURCE_DIR/safe_test_run.command" "$DEPLOY_FOLDER/safe_test_run.command"
    chmod +x "$DEPLOY_FOLDER/safe_test_run.command"
fi
if [ -f "$SOURCE_DIR/embed_art.command" ]; then
    cp -f "$SOURCE_DIR/embed_art.command" "$DEPLOY_FOLDER/embed_art.command"
    chmod +x "$DEPLOY_FOLDER/embed_art.command"
fi

# Copy tray launcher
if [ -f "$SOURCE_DIR/library_tray_launcher.py" ]; then
    cp -f "$SOURCE_DIR/library_tray_launcher.py" "$DEPLOY_FOLDER/library_tray_launcher.py"
fi

# Copy icons directory
if [ -d "$SOURCE_DIR/icons" ]; then
    echo "Copying icons directory..."
    cp -R "$SOURCE_DIR/icons" "$DEPLOY_FOLDER/"
fi

# Copy requirements
if [ -f "$SOURCE_DIR/requirements.txt" ]; then
    cp -f "$SOURCE_DIR/requirements.txt" "$DEPLOY_FOLDER/requirements.txt"
fi

echo ""
echo "========================================"
echo "Deployment Complete!"
echo "========================================"
echo ""
echo "Files deployed to: $DEPLOY_FOLDER"
echo ""
echo "To test:"
echo "  1. Activate venv: source ~/local_python_envs/t8sync/bin/activate"
echo "  2. cd \"$DEPLOY_FOLDER\""
echo "  3. python3 test_quick.py"
echo "  4. python3 main.py --mode normal --dry"
echo ""
echo "Or use your existing test scripts (safe_test_run.command, etc.)"
echo ""
read -p "Press Enter to continue..."

