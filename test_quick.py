#!/usr/bin/env python3
"""
Quick test script to verify all modules can be imported and basic functionality works.
Run this before doing full integration tests.
"""
import sys
from pathlib import Path

def test_imports():
    """Test that all modules can be imported.
    Returns: (success: bool, missing_deps: list)
    """
    print("Testing module imports...")
    print("  (Note: Some modules require dependencies - install with: pip install -r requirements.txt)")
    
    missing_deps = []
    
    try:
        import config
        print("  ✓ config")
    except Exception as e:
        print(f"  ✗ config: {e}")
        return False, missing_deps
    
    try:
        import logging_utils
        print("  ✓ logging_utils")
    except Exception as e:
        print(f"  ✗ logging_utils: {e}")
        return False
    
    try:
        import tag_operations
        print("  ✓ tag_operations")
    except ImportError as e:
        if 'mutagen' in str(e):
            print(f"  ⚠ tag_operations: Missing dependency 'mutagen'")
            missing_deps.append('mutagen')
        else:
            print(f"  ✗ tag_operations: {e}")
            return False, missing_deps
    except Exception as e:
        print(f"  ✗ tag_operations: {e}")
        return False, missing_deps
    
    try:
        import artwork
        print("  ✓ artwork")
    except ImportError as e:
        dep_name = None
        for dep in ['mutagen', 'musicbrainzngs', 'requests']:
            if dep in str(e):
                dep_name = dep
                break
        if dep_name:
            print(f"  ⚠ artwork: Missing dependency '{dep_name}'")
            if dep_name not in missing_deps:
                missing_deps.append(dep_name)
        else:
            print(f"  ✗ artwork: {e}")
            return False, missing_deps
    except Exception as e:
        print(f"  ✗ artwork: {e}")
        return False, missing_deps
    
    try:
        import file_operations
        print("  ✓ file_operations")
    except Exception as e:
        print(f"  ✗ file_operations: {e}")
        return False, missing_deps
    
    try:
        import sync_operations
        print("  ✓ sync_operations")
    except Exception as e:
        print(f"  ✗ sync_operations: {e}")
        return False, missing_deps
    
    try:
        import main
        print("  ✓ main")
    except Exception as e:
        print(f"  ✗ main: {e}")
        return False, missing_deps
    
    return True, missing_deps


def test_config():
    """Test that configuration is accessible."""
    print("\nTesting configuration...")
    
    try:
        import config
        print(f"  ✓ SYSTEM: {config.SYSTEM}")
        print(f"  ✓ MUSIC_ROOT: {config.MUSIC_ROOT}")
        print(f"  ✓ DOWNLOADS_DIR: {config.DOWNLOADS_DIR}")
        print(f"  ✓ T8_ROOT: {config.T8_ROOT}")
        return True
    except Exception as e:
        print(f"  ✗ Configuration error: {e}")
        return False


def test_logging_setup():
    """Test that logging can be set up."""
    print("\nTesting logging setup...")
    
    try:
        from logging_utils import setup_logging, log
        setup_logging()
        log("Test log message")
        print("  ✓ Logging setup works")
        return True
    except Exception as e:
        print(f"  ✗ Logging error: {e}")
        return False


def test_path_resolution():
    """Test that paths resolve correctly."""
    print("\nTesting path resolution...")
    
    try:
        from config import MUSIC_ROOT, DOWNLOADS_DIR, T8_ROOT
        from pathlib import Path
        
        print(f"  MUSIC_ROOT exists: {MUSIC_ROOT.exists()}")
        print(f"  DOWNLOADS_DIR exists: {DOWNLOADS_DIR.exists()}")
        if T8_ROOT:
            print(f"  T8_ROOT exists: {T8_ROOT.exists()}")
        else:
            print(f"  T8_ROOT: None (not configured)")
        
        print("  ✓ Path resolution works")
        return True
    except Exception as e:
        print(f"  ✗ Path resolution error: {e}")
        return False


def main():
    """Run all quick tests."""
    print("=" * 60)
    print("Quick Test Suite for Refactored Music Library Sync")
    print("=" * 60)
    
    results = []
    missing_deps = []
    
    imports_passed, missing_deps = test_imports()
    results.append(("Imports", imports_passed))
    results.append(("Configuration", test_config()))
    results.append(("Logging", test_logging_setup()))
    results.append(("Path Resolution", test_path_resolution()))
    
    print("\n" + "=" * 60)
    print("Test Results:")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False
    
    if missing_deps:
        print(f"\n⚠ Missing dependencies: {', '.join(missing_deps)}")
        print("  Install with: pip install -r requirements.txt")
        print("  Or install individually: pip install " + " ".join(missing_deps))
    
    print("=" * 60)
    if all_passed:
        print("✓ All quick tests passed!")
        print("\nNext step: Run full dry-run test:")
        print("  python main.py --mode normal --dry")
        return 0
    else:
        if missing_deps:
            print("\n⚠ Install missing dependencies, then re-run tests.")
        else:
            print("✗ Some tests failed. Fix issues before proceeding.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

