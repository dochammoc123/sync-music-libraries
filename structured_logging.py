"""
Structured logging API for music library sync script.

This module provides a new logging API that tracks headers, counts, album context, and item context,
writing to both detail logs and summary logs. Headers are only included in summaries
if they have at least one detail item (total_count > 0).

Key concepts:
- Step-level headers: Top-level headers set first (e.g., "Step 1: Process downloads (%count% albums)")
- Albums: Main headers/titles (set with set_album) - all warnings, errors, and nested headers go under albums
- Nested headers: Sub-headers pushed under albums (e.g., "Organizing (%count% songs)")
- Details: Item-level messages (e.g., "MOVE: %item% -> dest/file1.flac") - leaf level only
- Album context: Current album being processed (set once, all subsequent logs associated until unset)
- Item context: Current item being processed (leaf-level only, affects automatic counting)
- Header stack: Nested headers with independent counts (step -> album -> nested headers)
- Count tracking: Each header tracks direct items + children's items (propagated up)

Placeholders:
- %count% = Deferred replacement (replaced when header written to summary)
- %msg% = Template replacement (replaced with msg parameter when header template is processed)
- %item% = Deferred replacement (replaced when detail logged)
- {var} = Immediate replacement (Python f-string style, replaced when header/item created)

Usage:
    from structured_logging import logmsg
    
    # Step 1: Set step-level header (top level, logged to detail immediately)
    header_key = logmsg.set_header("Step 1: Process new downloads (%count% albums)")
    
    # Step 2: Set album context (main header/title - all warnings, errors, nested headers go under albums)
    album_key = logmsg.set_album("Lorde", "Pure Heroine", "2013")
    
    # Step 3: Push nested header under album (increases level)
    nested_key = logmsg.push_header("DOWNLOAD", "Organizing (%count% songs)", "Organizing")
    
    try:
        # Step 4: Set item context (leaf-level only - must unset before next item)
        item_key = logmsg.set_item(str(src))
        
        # Step 5: Log details (item automatically counted on first encounter)
        logmsg.info("MOVE: %item% -> {dest}", dest=str(dest))
        logmsg.info("Tags: artist={artist}, title={title}", artist=tags['artist'], title=tags['title'])
        # Multiple logs per item, but only counted once
        
        # Step 6: Unset item context (required before next item)
        logmsg.unset_item(item_key)
        
    finally:
        # Step 7: Pop nested header (writes to summary if count > 0, propagates count to album)
        logmsg.pop_header(nested_key)
        
        # Step 8: Unset album context (required before next album)
        logmsg.unset_album(album_key)
    
    # Step 9: Headers are only written to summary if at least one detail item was logged
    # Albums are the main headers - all warnings, errors, and nested headers appear under albums
"""
import logging
import logging.handlers
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from logging_utils import logger, ALBUM_SUMMARY, add_album_event_label, add_album_warning_label, album_label_from_tags, Colors, ColoredFormatter, PlainFormatter
from config import DETAIL_LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT, SYSTEM

# Detail log writer (separate from summary) - file handler only
_detail_logger = logging.getLogger("library_sync_detail")

# Console logger for structured logging (console output only, new API only)
_console_logger = logging.getLogger("library_sync_console")


def setup_detail_logging() -> None:
    """
    Configure the new structured logging API:
    - Detail logger: writes to DETAIL_LOG_FILE (file only)
    - Console logger: writes to console with colored formatter
    """
    # Enable Windows ANSI colors if on Windows
    if SYSTEM == "Windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            pass  # If it fails, colors just won't work - not critical
    
    # Setup detail logger (file only)
    _detail_logger.setLevel(logging.INFO)
    _detail_logger.handlers.clear()  # Remove any existing handlers
    
    if DETAIL_LOG_FILE is not None:
        DETAIL_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        detail_fh = RotatingFileHandler(
            DETAIL_LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        detail_fh.setFormatter(PlainFormatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
        _detail_logger.addHandler(detail_fh)
        _detail_logger.propagate = False  # Don't propagate to root logger
    
    # Setup console logger (console only, with colors)
    _console_logger.setLevel(logging.INFO)
    _console_logger.handlers.clear()  # Remove any existing handlers
    
    import sys
    console_sh = logging.StreamHandler(sys.stdout)
    console_sh.setFormatter(ColoredFormatter("%(message)s"))
    _console_logger.addHandler(console_sh)
    _console_logger.propagate = False  # Don't propagate to root logger


@dataclass
class HeaderDefinition:
    """Represents a header definition (like a class/template)."""
    header_key: str  # Unique identifier for this header definition
    category: Optional[str]  # e.g., "DOWNLOAD", "UPDATE", or None
    message_template: str  # e.g., "Step 1: Process downloads (%count% items)" (for summary)
    count_placeholder: str  # Default: "%count%" (deferred replacement)
    detail_message: str  # Message for detail log (without %count% placeholder)
    level: int  # Nesting level (0 = step, 1 = sub-album, etc.)


@dataclass
class HeaderInstance:
    """Represents a header instance (runtime instance with counts, album association)."""
    header_key: str  # References HeaderDefinition.header_key
    album_label: Optional[str]  # Album label (None for global)
    instance_key: str  # Unique key for this instance (for stack tracking)
    count: int = 0  # Number of unique items logged under this header instance
    counted_items: set = field(default_factory=set)  # Track item IDs to prevent double-counting
    detail_messages: List[str] = field(default_factory=list)  # Accumulated detail messages
    
    def should_log(self) -> bool:
        """Should this header instance be logged? (has items)"""
        return self.count > 0


class StructuredLogger:
    """
    Structured logger that tracks headers, counts, album context, and item context.
    
    This logger maintains:
    - Header definitions (templates/classes) - what defines a header
    - Header instances (runtime objects) - instances per (header_key, album_label)
    - Active header definitions stack - which definitions are currently active
    - Active header instances stack - current instances for active album
    - Album context (affects which instances are active)
    - Item context (affects automatic counting)
    
    Headers are only included in summaries if they have count > 0.
    """
    
    def __init__(self):
        # Header definitions (templates/classes) - keyed by header_key
        self.header_definitions: Dict[str, HeaderDefinition] = {}
        
        # Header instances (runtime objects) - keyed by (header_key, album_label)
        self.header_instances: Dict[Tuple[str, Optional[str]], HeaderInstance] = {}
        
        # Active header definitions stack (the hierarchy/template)
        # Represents which header definitions are currently in the processing hierarchy
        self.active_definition_stack: List[str] = []  # List of header_key values
        
        # Active header instances stack (current instances for current album)
        # Represents the current active instances being processed. When pop_header() is called,
        # the instance is written to summary (if count > 0) and removed from this active stack,
        # but the instance remains in the registry. This stack tracks the current nesting/hierarchy
        # during processing. When set_album() is called, this stack is refreshed with instances
        # for that album (creating new ones or reusing existing ones from the registry).
        self.active_instance_stack: List[HeaderInstance] = []
        
        # Album context
        self.current_album_label: Optional[str] = None
        self.current_album_info: Optional[Tuple[str, str, Optional[str]]] = None  # (artist, album, year)
        self._current_album_key: Optional[str] = None  # Key for current album (for sanity checking)
        
        # Item context
        self.current_item_id: Optional[str] = None  # Current item context
        self._current_item_key: Optional[str] = None  # Key for current item (for sanity checking)
    
    def _get_or_create_instance(self, header_key: str, album_label: Optional[str]) -> HeaderInstance:
        """Get or create a header instance for the given header_key and album_label."""
        instance_key = (header_key, album_label)
        if instance_key not in self.header_instances:
            # Create new instance
            instance = HeaderInstance(
                header_key=header_key,
                album_label=album_label,
                instance_key=str(uuid.uuid4())
            )
            self.header_instances[instance_key] = instance
        return self.header_instances[instance_key]
    
    def _refresh_active_instances(self) -> None:
        """
        Refresh active_instance_stack based on active_definition_stack and current_album_label.
        Creates/reuses instances for all active definitions for the current album.
        """
        self.active_instance_stack = []
        for header_key in self.active_definition_stack:
            instance = self._get_or_create_instance(header_key, self.current_album_label)
            self.active_instance_stack.append(instance)
        
    def set_album(self, artist: str, album: str, year: Optional[str] = None) -> str:
        """
        Set the current album context.
        Must call unset_album(key) before calling set_album again (except first call).
        
        When set_album() is called, it creates/reuses header instances for all currently active
        header definitions for this album. This allows the same header definition (e.g., "Step 1")
        to have separate instances with separate counts for each album processed.
        
        All subsequent logs (with item_id) will increment counts on the active instances for this album.
        Warnings/errors will be associated with the current album.
        
        WARNINGS/ERRORS FOLLOW CURRENT ALBUM CONTEXT:
        When warnings or errors are logged, they follow the current album context.
        If an album is set, the warning/error is added to that album's summary.
        If no album is set (global), the warning/error is added to the global summary.
        
        Args:
            artist: Artist name
            album: Album name
            year: Year (optional, can be None or "")
        
        Returns:
            str: Key to use with unset_album() (for sanity check)
        
        Raises:
            ValueError: If album is already set (must call unset_album(key) first)
        
        Examples:
            # In main() - step header that processes MULTIPLE albums:
            header_key = logmsg.set_header("Step 1", "%msg% (%count% items)")
            # This header definition will have separate instances for each album
            
            # In process_downloads() - loop through albums:
            for album in albums:
                album_key = logmsg.set_album("Artist", "Album", "2023")  # Creates/reuses instances for this album
                # All active header definitions now have instances for this album
                # Logs with item_id will increment counts on these instances
                # ... process album ...
                logmsg.unset_album(album_key)  # REQUIRED: must unset before processing next album
            
            # Each album will have its own "Step 1" instance with its own count in the summary
        """
        # If an album is currently set, must unset it first
        if self.current_album_info is not None:
            raise ValueError(f"Cannot set_album when album is already set ({self.current_album_label}). "
                           f"Must call unset_album(key) first.")
        
        # Generate a unique key for this album session
        album_key = str(uuid.uuid4())
        
        # Set album context
        self.current_album_info = (artist, album, year)
        album_label = album_label_from_tags(artist, album, year or "")
        self.current_album_label = album_label
        self._current_album_key = album_key
        
        return album_key
    
    def unset_album(self, key: str) -> None:
        """
        Unset the current album context (makes it global for future headers/logs).
        Must provide the key returned from set_album().
        Creates/reuses global header instances (album_label=None) for all active definitions.
        
        This ensures that when returning from a processing method back to main(), instances
        become global (not associated with the previous album).
        
        Args:
            key: Key returned from set_album() (sanity check)
        
        Raises:
            ValueError: If key doesn't match current album key, or if no album is currently set
        """
        if self.current_album_info is None:
            raise ValueError("Cannot unset_album: no album is currently set")
        
        if self._current_album_key != key:
            raise ValueError(f"Album key mismatch: expected {self._current_album_key}, got {key}")
        
        # Clear album context
        self.current_album_info = None
        self.current_album_label = None
        self._current_album_key = None
        
        # Refresh active instances (creates/reuses global instances for all active definitions)
        self._refresh_active_instances()
    
    def set_item(self, item: str) -> str:
        """
        Set the current item context (leaf-level only - no nested items).
        Must call unset_item(key) before calling set_item again (except first call).
        All subsequent logs will use this item for counting.
        
        Args:
            item: Item identifier (e.g., file path, song number)
        
        Returns:
            str: Key to use with unset_item() (for sanity check)
        
        Raises:
            ValueError: If item is already set (must call unset_item(key) first)
        
        Note: Items can only be set at the lowest level (no nested items). This helps ensure
        headers are only written if at least one detail item is logged.
        """
        # If an item is currently set, must unset it first
        if self.current_item_id is not None:
            raise ValueError(f"Cannot set_item when item is already set ({self.current_item_id}). "
                           f"Must call unset_item(key) first.")
        
        # Generate a unique key for this item session
        item_key = str(uuid.uuid4())
        
        # Set new item
        self.current_item_id = item
        self._current_item_key = item_key
        
        return item_key
    
    def unset_item(self, key: str) -> None:
        """
        Unset the current item context.
        Must provide the key returned from set_item().
        
        Args:
            key: Key returned from set_item() (sanity check)
        
        Raises:
            ValueError: If key doesn't match current item key, or if no item is currently set
        """
        if self.current_item_id is None:
            raise ValueError("Cannot unset_item: no item is currently set")
        
        if self._current_item_key != key:
            raise ValueError(f"Item key mismatch: expected {self._current_item_key}, got {key}")
        
        # Clear item
        self.current_item_id = None
        self._current_item_key = None
    
    def set_header(
        self, 
        msg: str,
        message_template: Optional[str] = None,
        count_placeholder: str = "%count%",
        key: Optional[str] = None
    ) -> str:
        """
        Set/replace the current header at the current level.
        Shortcut for: pop_header(key) (if key provided) + push_header() at same level.
        
        Use for headers like "Step 1: Process downloads (%count% items)"
        
        Note: %count% is replaced LATER when header is written to summary.
        Use {var} for IMMEDIATE replacement (Python str.format() style, e.g., {artist}, {album}, {year}).
        
        Args:
            msg: Message for detail log. Also used as template if message_template is None.
            message_template: Optional header message template for summary log with:
                - %msg% for template replacement (replaced with msg parameter)
                - %count% for deferred count replacement (replaced when header written to summary)
                - {var} for immediate replacement from album context (e.g., {artist}, {album}, {year})
                If None, msg is used as the template.
            count_placeholder: Placeholder for count (default: "%count%")
            key: Optional header_key from previous set_header() call. If provided and stack is not empty,
                must match the current header's key (sanity check). Use None for first call.
        
        Returns:
            str: header_key to use with next set_header() call (for sanity check)
        
        Raises:
            ValueError: If key is provided but doesn't match current header, or if key is provided but stack is empty
        
        Examples:
            key = set_header("H1") -> detail: "H1", summary: "H1" (first call, key=None)
            key2 = set_header("H2", key=key) -> detail: "H2", summary: "H2" (replaces previous)
            set_header("H1", "H1 .. %count%") -> detail: "H1", summary: "H1 .. 2"
            set_header("H1", "%msg% (count = %count%)") -> detail: "H1", summary: "H1 (count = 2)"
        
        Note: %item% placeholder is only for detail logs (info/warn/error), not headers.
        """
        # If key provided, pop current header (sanity check)
        if key is not None:
            if not self.active_definition_stack:
                raise ValueError(f"Cannot set_header with key {key}: definition stack is empty")
            
            current_key = self.active_definition_stack[-1]
            if current_key != key:
                raise ValueError(f"Header key mismatch: expected {current_key}, got {key}")
            
            # Pop current header from stacks (but don't write summary - we're replacing it)
            self.active_definition_stack.pop()
            self.active_instance_stack.pop()
        
        # Use msg as template if message_template not provided
        template = message_template if message_template is not None else msg
        
        # Push new header at same level (or level 0 if was empty)
        # push_header will handle %msg% replacement and immediate replacements
        new_key = self.push_header(msg, template, None, count_placeholder)
        return new_key
    
    def _format_immediate_replacements(self, message: str) -> str:
        """
        Format immediate replacements using Python str.format() style.
        Builds a dict from current album context (artist, album, year) and formats any {var} placeholders.
        Leaves %count% placeholder untouched for later replacement.
        """
        # Build dict of available variables from album context
        format_dict = {}
        if self.current_album_info:
            artist, album, year = self.current_album_info
            format_dict = {
                "artist": artist,
                "album": album,
                "year": year if year else ""
            }
        
        # Protect deferred placeholders (%count%) by temporarily replacing them
        protected = message
        placeholder_map = {}
        for placeholder in ["%count%", "%Count%"]:
            if placeholder in protected:
                temp = f"__PROTECTED_{len(placeholder_map)}__"
                placeholder_map[temp] = placeholder
                protected = protected.replace(placeholder, temp)
        
        # Use Python's format() to replace any {var} placeholders
        try:
            formatted = protected.format(**format_dict)
        except KeyError as e:
            # If a variable is not in the dict, leave it as-is (don't error)
            formatted = protected
        except ValueError:
            # If there's a format error (e.g., unmatched braces), leave as-is
            formatted = protected
        
        # Restore deferred placeholders
        for temp, original in placeholder_map.items():
            formatted = formatted.replace(temp, original)
        
        return formatted
    
    def push_header(
        self,
        msg: str,
        message_template: Optional[str] = None,
        category: Optional[str] = None,
        count_placeholder: str = "%count%"
    ) -> str:
        """
        Push a nested header onto the stack (increases level).
        Creates a header definition and adds it to active definitions stack.
        Creates/refreshes instances for current album (or global if no album set).
        Formats immediate replacements using any {var} placeholders but leaves %count% for later.
        
        Args:
            msg: Message for detail log. Also used as template if message_template is None.
            message_template: Optional header message template for summary log with placeholders:
                - %msg% for template replacement (replaced with msg parameter)
                - %count% for deferred count replacement (replaced when header written to summary)
                - {var} for immediate replacement from album context (e.g., {artist}, {album}, {year})
                If None, msg is used as the template.
            category: Header category (e.g., "DOWNLOAD", "UPDATE"), or None
            count_placeholder: Placeholder for count (default: "%count%")
        
        Returns:
            str: header_key to use with pop_header() (for sanity check)
        """
        # Generate unique header_key for this definition
        header_key = str(uuid.uuid4())
        level = len(self.active_definition_stack)
        
        # Use msg as template if message_template not provided
        template = message_template if message_template is not None else msg
        
        # Replace %msg% in template with msg
        formatted_template = template.replace("%msg%", msg)
        formatted_template = formatted_template.replace("%Msg%", msg)  # Case variation
        
        # Format immediate replacements ({vars}) for template
        formatted_template = self._format_immediate_replacements(formatted_template)
        
        # Format immediate replacements for detail message
        formatted_detail = self._format_immediate_replacements(msg)
        
        # Create or update header definition
        definition = HeaderDefinition(
            header_key=header_key,
            category=category,
            message_template=formatted_template,
            count_placeholder=count_placeholder,
            detail_message=formatted_detail,
            level=level
        )
        self.header_definitions[header_key] = definition
        
        # Add to active definitions stack
        self.active_definition_stack.append(header_key)
        
        # Refresh active instances (creates/reuses instances for current album)
        self._refresh_active_instances()
        
        # Log header to detail log immediately (using definition)
        if self.active_instance_stack:
            instance = self.active_instance_stack[-1]
            self._log_header_to_detail(definition, instance)
        
        return header_key
    
    def pop_header(self, header_key: str) -> None:
        """
        Pop a header from the stack (decreases level).
        Writes instance to summary if should_log() is True.
        
        Args:
            header_key: The header_key returned from push_header() (sanity check)
        
        Raises:
            ValueError: If header_key doesn't match the top of the stack
        """
        if not self.active_definition_stack:
            raise ValueError("Cannot pop header: definition stack is empty")
        
        if not self.active_instance_stack:
            raise ValueError("Cannot pop header: instance stack is empty")
        
        # Verify key matches
        top_key = self.active_definition_stack[-1]
        if top_key != header_key:
            raise ValueError(f"Header key mismatch: expected {top_key}, got {header_key}")
        
        # Get definition and instance
        definition = self.header_definitions[header_key]
        instance = self.active_instance_stack[-1]
        
        # Write instance to summary if it should be logged
        if instance.should_log():
            self._write_header_to_summary(definition, instance)
        
        # Pop from both stacks
        self.active_definition_stack.pop()
        self.active_instance_stack.pop()
    
    def _log_header_to_detail(self, definition: HeaderDefinition, instance: HeaderInstance) -> None:
        """
        Log a header to the detail log immediately (without %count% placeholder).
        Used when header is first set/pushed.
        Headers are written to both detail file and console (new API only).
        """
        # Use detail_message from definition
        message = definition.detail_message
        
        # Get indentation and prefix based on header level
        indent = self._get_indent_for_level(definition.level)
        prefix = self._get_prefix_for_level(definition.level)
        
        formatted = f"{indent}{prefix}{message}"
        
        # Write to both detail file and console (new API)
        _detail_logger.info(formatted)
        _console_logger.info(formatted)
    
    def _write_header_to_summary(self, definition: HeaderDefinition, instance: HeaderInstance) -> None:
        """
        Write a header instance to the summary log (album summary or global).
        Only called if instance.should_log() is True.
        """
        # Replace count placeholder with instance count
        message = definition.message_template.replace(definition.count_placeholder, str(instance.count))
        # Also replace lowercase/uppercase variations
        if definition.count_placeholder == "%count%":
            message = message.replace("%Count%", str(instance.count))
        elif definition.count_placeholder == "%Count%":
            message = message.replace("%count%", str(instance.count))
        
        if instance.album_label:
            # Write to album summary (old API summary structure)
            add_album_event_label(instance.album_label, message)
        else:
            # Global headers: write to detail log and console (new API)
            # Note: Already has [INFO] prefix from above
            _detail_logger.info(message)
            _console_logger.info(message)
    
    def _get_indent_for_level(self, level: int) -> str:
        """Get indentation string for a given header level."""
        if level == 0:
            return ""
        elif level == 1:
            return "  "
        elif level == 2:
            return "    "
        else:
            return "  " * level  # Fallback for deeper nesting
    
    def _get_prefix_for_level(self, level: int) -> str:
        """Get prefix (chevrons) for a given header level."""
        if level == 0:
            return ""
        elif level == 1:
            return "> "
        elif level == 2:
            return ">> "
        else:
            return ">" * level + " "
    
    def _get_indent(self) -> str:
        """Get indentation string based on current header stack level."""
        level = len(self.active_instance_stack)
        return self._get_indent_for_level(level)
    
    def _get_prefix(self) -> str:
        """Get prefix (chevrons) based on current header stack level."""
        level = len(self.active_instance_stack)
        return self._get_prefix_for_level(level)
    
    
    def _log_detail(
        self,
        message: str,
        level: str,
        album: Optional[str] = None,
        item: Optional[str] = None,
        console: bool = True,
        **kwargs
    ) -> None:
        """
        Internal method to log a detail message at the specified level.
        Shared logic for info, warn, error, and verbose methods.
        
        Args:
            message: Message to log (can contain %item% placeholder and {var} placeholders)
            level: Log level ("info", "warn", "error", or "verbose")
            album: Optional album label (overrides current album context, "" = global)
            item: Optional item identifier (overrides current item context, "" = no item)
            console: If True, write to console logger; if False, write to detail logger only
            **kwargs: Additional named parameters for {var} placeholder replacement
        """
        # Determine album to use for warnings/errors:
        # 1. Explicit album parameter (overrides everything)
        # 2. Current album context (set via set_album())
        # This ensures warnings/errors follow the current album context
        if album == "":  # Explicitly clear (force global)
            use_album = None
        elif album is not None:  # Explicitly provided
            use_album = album
        else:  # Use current album context (or None)
            use_album = self.current_album_label
        
        # Determine item to use (parameter overrides context)
        use_item = item if item is not None else self.current_item_id
        if item == "":  # Explicitly clear
            use_item = None
        
        # Format message with immediate replacements ({var}) first, then item placeholder
        # Build format dict from album context and kwargs
        format_dict = {}
        if self.current_album_info:
            artist, album_name, year = self.current_album_info
            format_dict.update({
                "artist": artist,
                "album": album_name,
                "year": year or ""
            })
        # Add any kwargs (like album_dir=str(album_dir))
        format_dict.update(kwargs)
        
        # Format {var} placeholders using str.format()
        formatted_message = message
        if format_dict:
            try:
                formatted_message = formatted_message.format(**format_dict)
            except (KeyError, ValueError):
                # If format fails (missing key or syntax error), leave as-is
                pass
        
        # Then format %item% placeholder
        if use_item:
            formatted_message = formatted_message.replace("%item%", str(use_item))
            formatted_message = formatted_message.replace("%Item%", str(use_item))  # Case variations
        else:
            formatted_message = formatted_message.replace("%item%", "")
            formatted_message = formatted_message.replace("%Item%", "")
        
        # Build prefix based on level
        level_prefix = {
            "info": "[INFO] ",
            "verbose": "[VERBOSE] ",
            "warn": "[WARN] ",
            "error": "[ERROR] "
        }.get(level, "[INFO] ")
        
        indent = self._get_indent()
        prefix = self._get_prefix()
        formatted = f"{indent}{prefix}{level_prefix}{formatted_message}"
        
        # Write to appropriate logger(s)
        if console:
            # Write to console logger (new API console output only)
            log_method = {
                "info": _console_logger.info,
                "verbose": _console_logger.info,  # Same as info level
                "warn": _console_logger.warning,
                "error": _console_logger.error
            }.get(level, _console_logger.info)
            log_method(formatted)
            # Also write to detail file
            _detail_logger.info(formatted)
        else:
            # Write to detail logger only (file only, no console)
            _detail_logger.info(formatted)
        
        # Add to album/global warnings for warn/error
        if level in ("warn", "error"):
            if use_album:
                add_album_warning_label(use_album, formatted_message, level=level)
            else:
                from logging_utils import add_global_warning
                add_global_warning(formatted_message, level=level)
        
        # Add to current header instance's detail messages
        if self.active_instance_stack:
            self.active_instance_stack[-1].detail_messages.append(formatted)
        
        # Automatically count item if item provided (first encounter)
        if use_item:
            self._increment_current_count(use_item)
    
    def info(self, message: str, album: Optional[str] = None, item: Optional[str] = None, **kwargs) -> None:
        """
        Log an info-level detail message.
        Automatically counts item if item is provided (first encounter per header).
        
        Args:
            message: Message to log (can contain %item% placeholder and {var} placeholders)
            album: Optional album label (overrides current album context, "" = global)
            item: Optional item identifier (overrides current item context, "" = no item)
                 If provided and first encounter, automatically increments count
            **kwargs: Additional named parameters for {var} placeholder replacement
        """
        self._log_detail(message, "info", album, item, **kwargs)
    
    def warn(self, message: str, album: Optional[str] = None, item: Optional[str] = None, **kwargs) -> None:
        """
        Log a warning-level detail message.
        Automatically counts item if item is provided (first encounter per header).
        
        Args:
            message: Message to log (can contain %item% placeholder and {var} placeholders)
            album: Optional album label (overrides current album context, "" = global)
            item: Optional item identifier (overrides current item context, "" = no item)
            **kwargs: Additional named parameters for {var} placeholder replacement
        """
        self._log_detail(message, "warn", album, item, **kwargs)
    
    def error(self, message: str, album: Optional[str] = None, item: Optional[str] = None, **kwargs) -> None:
        """
        Log an error-level detail message.
        Automatically counts item if item is provided (first encounter per header).
        
        Args:
            message: Message to log (can contain %item% placeholder and {var} placeholders)
            album: Optional album label (overrides current album context, "" = global)
            item: Optional item identifier (overrides current item context, "" = no item)
            **kwargs: Additional named parameters for {var} placeholder replacement
        """
        self._log_detail(message, "error", album, item, **kwargs)
    
    def verbose(self, message: str, album: Optional[str] = None, item: Optional[str] = None, **kwargs) -> None:
        """
        Log a verbose/trace-level detail message (file only, not console).
        Identical to info() in all respects (counting, header messages, etc.) except console output.
        Useful for detailed tracing that's available in logs but doesn't clutter console output.
        
        Args:
            message: Message to log (can contain %item% placeholder and {var} placeholders)
            album: Optional album label (overrides current album context, "" = global)
            item: Optional item identifier (overrides current item context, "" = no item)
                 If provided and first encounter, automatically increments count (same as info)
            **kwargs: Additional named parameters for {var} placeholder replacement
        
        Note: Verbose messages are written to the detail log file only, not to console.
        They are useful for tracing code execution without cluttering console output.
        Items are counted and messages are added to headers just like info() calls.
        """
        self._log_detail(message, "verbose", album, item, console=False, **kwargs)
    
    def _increment_current_count(self, item_id: str) -> None:
        """
        Increment count for all active header instances if item_id hasn't been counted yet.
        This is called automatically when logging with an item_id.
        
        Args:
            item_id: Identifier for the item (must be provided)
        """
        if not self.active_instance_stack or not item_id:
            return
        
        # Increment count for all active instances (all headers on stack)
        for instance in self.active_instance_stack:
            # Check if this item was already counted in this instance
            if item_id not in instance.counted_items:
                instance.count += 1
                instance.counted_items.add(item_id)
    
    def clear(self) -> None:
        """Clear the header stacks and album context. Useful for testing."""
        self.header_definitions.clear()
        self.header_instances.clear()
        self.active_definition_stack.clear()
        self.active_instance_stack.clear()
        self.current_album_label = None
        self.current_album_info = None
        self._current_album_key = None


# Global singleton instance
logmsg = StructuredLogger()

