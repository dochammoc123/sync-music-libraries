# Bug in sync_operations.py - apply_updates_from_overlay()

**Location**: Lines 130-165  
**Function**: `apply_updates_from_overlay()`  
**Severity**: **CRITICAL** - Will cause NameError at runtime

## The Bug

### Problem 1: Variable Scope Error (Line 144)
```python
if dest.exists():
    src_freq = get_sample_rate(src)
    dest_freq = get_sample_rate(dest)
    # ... frequency comparison logic ...
    should_copy = True
elif src_freq < dest_freq:  # ❌ BUG: src_freq/dest_freq don't exist if dest.exists() is False!
    should_copy = False
```

**Issue**: `src_freq` and `dest_freq` are only defined inside the `if dest.exists():` block, but line 144 uses `elif src_freq < dest_freq:` which references these variables. If `dest.exists()` is False, these variables don't exist → **NameError**.

### Problem 2: Logic Flow Error
The `elif` on line 144 should be checking frequency comparison **inside** the `if dest.exists():` block, not as an alternative to it.

### Problem 3: Wrong Indentation (Lines 148-156)
The `else:` block (lines 148-156) that handles "same frequency, compare file size" is at the wrong indentation level. It should be inside the `if dest.exists():` block.

### Problem 4: Missing Logic
The code doesn't handle the case where:
- `dest.exists()` is False → should copy (file doesn't exist yet)
- `dest.exists()` is True but frequencies can't be determined → should fall back to size comparison

## Current (Broken) Structure

```python
if dest.exists():
    src_freq = get_sample_rate(src)
    dest_freq = get_sample_rate(dest)
    if src_freq and dest_freq:
        if src_freq > dest_freq:
            upgrade_reason.append(...)
    should_copy = True
elif src_freq < dest_freq:  # ❌ BUG: variables don't exist here!
    should_copy = False
else:  # ❌ Wrong indentation level
    # Same frequency, compare file size
    ...
else:  # ❌ This is for "can't determine frequency"
    # Fall back to file size only
    ...
```

## Correct Structure Should Be

```python
if dest.exists():
    src_freq = get_sample_rate(src)
    dest_freq = get_sample_rate(dest)
    
    if src_freq and dest_freq:
        # Both frequencies available - compare them
        if src_freq > dest_freq:
            upgrade_reason.append(f"frequency: {src_freq}Hz > {dest_freq}Hz")
            should_copy = True
        elif src_freq < dest_freq:
            should_copy = False
            log("SKIP: existing has higher frequency")
        else:
            # Same frequency - compare file size
            if src_size > dest_size:
                upgrade_reason.append(f"size: {src_size} > {dest_size} bytes")
                should_copy = True
            else:
                should_copy = False
                log("SKIP: same frequency, existing file is larger")
    else:
        # Can't determine frequency - fall back to file size only
        if src_size > dest_size:
            upgrade_reason.append(f"size: {src_size} > {dest_size} bytes")
            should_copy = True
        else:
            should_copy = False
            log("SKIP: existing file is larger")
else:
    # Destination doesn't exist - copy it
    should_copy = True
```

## Impact

- **Runtime Error**: Will crash with `NameError: name 'src_freq' is not defined` when processing audio files where destination doesn't exist
- **Logic Error**: Frequency comparison logic is broken even when variables exist
- **Missing Functionality**: Doesn't properly handle all comparison cases

## Test Cases to Verify Fix

### Before Fix (Should Fail)
1. Audio file in UPDATE_ROOT where destination doesn't exist → **NameError**
2. Audio file where destination exists but source has lower frequency → Logic error
3. Audio file where frequencies are same but sizes differ → May not work correctly

### After Fix (Should Pass)
1. Audio file where destination doesn't exist → Should copy
2. Audio file where source frequency > dest frequency → Should upgrade
3. Audio file where source frequency < dest frequency → Should skip
4. Audio file where frequencies same, source size > dest size → Should upgrade
5. Audio file where frequencies same, source size <= dest size → Should skip
6. Audio file where frequencies can't be determined → Should compare sizes
7. Non-audio file (artwork) → Should copy normally

## How to Test

```bash
# Test 1: Dry-run with UPDATE_ROOT containing audio files
python main.py --mode normal --dry

# Test 2: Real run with test audio file where dest doesn't exist
# (This will trigger the NameError before fix)

# Test 3: Real run with test audio file where dest exists
# (This will test the comparison logic)
```

