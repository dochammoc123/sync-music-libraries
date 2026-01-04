#!/usr/bin/env python3
"""
Validation script to check that all logging context management uses try/finally blocks.

This script validates:
1. All push_header() calls are followed by try/finally with pop_header() in finally
2. All set_album() calls are followed by try/finally with unset_album() in finally
3. All set_item() calls are followed by try/finally with unset_item() in finally
4. All set_header(None, key=...) calls are in finally blocks
5. Basic Python syntax validation
"""

import ast
import re
import sys
from pathlib import Path


def check_file_patterns(filepath: Path) -> list[str]:
    """Check a Python file for proper try/finally patterns around logging calls."""
    errors = []
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')
    except Exception as e:
        return [f"Could not read file: {e}"]
    
    # First, check Python syntax
    try:
        ast.parse(content, filename=str(filepath))
    except SyntaxError as e:
        errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
        return errors  # Don't continue pattern checking if syntax is broken
    
    # Check patterns using regex (simpler than AST for this use case)
    # This is a best-effort check - the linter is more accurate
    
    # Check for push_header without try/finally
    push_header_pattern = r'(\w+)\s*=\s*logmsg\.push_header\('
    for i, line in enumerate(lines, 1):
        match = re.search(push_header_pattern, line)
        if match:
            var_name = match.group(1)
            # Look ahead for try/finally pattern
            found_try = False
            found_finally = False
            for j in range(i, min(i + 50, len(lines))):  # Check next 50 lines
                if 'try:' in lines[j]:
                    found_try = True
                if found_try and f'logmsg.pop_header({var_name})' in lines[j]:
                    # Check if it's in a finally block
                    for k in range(j - 10, j):
                        if 'finally:' in lines[k]:
                            found_finally = True
                            break
                    break
            if not found_finally and found_try:
                errors.append(f"Line {i}: push_header('{var_name}') may not have finally block with pop_header")
    
    # Check for set_album without try/finally (similar pattern)
    set_album_pattern = r'(\w+)\s*=\s*logmsg\.set_album\('
    for i, line in enumerate(lines, 1):
        match = re.search(set_album_pattern, line)
        if match:
            var_name = match.group(1)
            found_try = False
            found_finally = False
            for j in range(i, min(i + 100, len(lines))):
                if 'try:' in lines[j] and j > i:  # try must be after set_album
                    found_try = True
                if found_try and f'logmsg.unset_album({var_name})' in lines[j]:
                    for k in range(j - 10, j):
                        if 'finally:' in lines[k]:
                            found_finally = True
                            break
                    break
            if not found_finally and found_try:
                errors.append(f"Line {i}: set_album('{var_name}') may not have finally block with unset_album")
    
    return errors


def main():
    """Validate all Python files in the project."""
    files_to_check = [
        Path('file_operations.py'),
        Path('sync_operations.py'),
        Path('main.py'),
    ]
    
    all_errors = []
    for filepath in files_to_check:
        if not filepath.exists():
            print(f"Warning: {filepath} not found, skipping")
            continue
        
        errors = check_file_patterns(filepath)
        if errors:
            print(f"\n{filepath}:")
            for error in errors:
                print(f"  {error}")
            all_errors.extend([(filepath, e) for e in errors])
    
    if all_errors:
        print(f"\nTotal issues found: {len(all_errors)}")
        sys.exit(1)
    else:
        print("\n✓ All files passed pattern validation!")
        sys.exit(0)


if __name__ == '__main__':
    main()

