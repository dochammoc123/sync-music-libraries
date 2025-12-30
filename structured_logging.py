"""
Structured logging API for music library sync script.

This module provides a new logging API that tracks headers, counts, album context, and item context,
writing to both detail logs and summary logs. Headers are only included in summaries
if they have at least one detail item (total_count > 0).

Key concepts:
- Headers: Summary-level messages with counts (e.g., "Step 1: Process downloads (%count% albums)")
- Details: Item-level messages (e.g., "MOVE: %item% -> dest/file1.flac")
- Album context: Current album being processed (set once, used for all subsequent logs)
- Item context: Current item being processed (affects automatic counting)
- Header stack: Nested headers with independent counts
- Count tracking: Each header tracks direct items + children's items (propagated up)

Placeholders:
- %count% = Deferred replacement (replaced when header written to summary)
- %msg% = Template replacement (replaced with msg parameter when header template is processed)
- %item% = Deferred replacement (replaced when detail logged)
- {var} = Immediate replacement (Python f-string style, replaced when header/item created)

Usage:
    from structured_logging import logmsg
    
    # Set album context
    logmsg.set_album("Lorde", "Pure Heroine", "2013")
    
    # Set header
    logmsg.set_header("Step 1: Process new downloads (%count% albums)")
    
    # Push album header (increases level)
    key = logmsg.push_header("DOWNLOAD", f"Organizing: {artist} - {album} ({year})", "%count% songs")
    
    try:
        # Set item context
        logmsg.set_item(str(src))
        
        # Log details (item automatically counted on first encounter)
        logmsg.info("MOVE: %item% -> {dest}", dest=str(dest))
        logmsg.info("Tags: artist={artist}, title={title}", artist=tags['artist'], title=tags['title'])
        # Multiple logs per item, but only counted once
        
    finally:
        logmsg.pop_header(key)  # Writes header if count > 0, propagates to step
        logmsg.set_item("")  # Clear item context
"""
import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from logging_utils import logger, ALBUM_SUMMARY, add_album_event_label, add_album_warning_label, album_label_from_tags

# Detail log writer (separate from summary)
_detail_logger = logging.getLogger("library_sync_detail")


@dataclass
class HeaderContext:
    """Represents a header context in the stack."""
    key: str
    category: Optional[str]  # e.g., "DOWNLOAD", "UPDATE", or None
    message_template: str  # e.g., "Step 1: Process downloads (%count% albums)" (for summary)
    count_placeholder: str  # Default: "%count%" (deferred replacement)
    level: int  # Nesting level (0 = step, 1 = album, 2 = sub-album, etc.)
    detail_message: Optional[str] = None  # Optional detail log message (if None, derived from template)
    count: int = 0  # Number of direct detail items logged under this header
    child_count: int = 0  # Sum of counts from child headers (propagated up)
    counted_items: set = field(default_factory=set)  # Track item IDs to prevent double-counting
    detail_messages: List[str] = field(default_factory=list)  # Accumulated detail messages
    album_label: Optional[str] = None  # Album label if this header is album-specific
    
    def total_count(self) -> int:
        """Total count including direct items and children."""
        return self.count + self.child_count
    
    def should_log(self) -> bool:
        """Should this header be logged? (has items or children with items)"""
        return self.total_count() > 0


class StructuredLogger:
    """
    Structured logger that tracks headers, counts, album context, and item context.
    
    This logger maintains:
    - Stack of headers (nested levels)
    - Album context (affects where headers are written)
    - Item context (affects automatic counting)
    - Counts per header (direct items + children)
    
    Headers are only included in summaries if they have total_count > 0.
    """
    
    def __init__(self):
        self.header_stack: List[HeaderContext] = []
        self.current_album_label: Optional[str] = None
        self.current_album_info: Optional[Tuple[str, str, Optional[str]]] = None  # (artist, album, year)
        self.current_item_id: Optional[str] = None  # Current item context
        
    def set_album(self, artist: Optional[str] = None, album: Optional[str] = None, year: Optional[str] = None) -> None:
        """
        Set the current album context.
        All subsequent logs will be associated with this album until changed.
        
        Args:
            artist: Artist name (or None/"" to clear album context)
            album: Album name (or None/"" to clear)
            year: Year (optional, can be None or "")
        
        Examples:
            logmsg.set_album("Lorde", "Pure Heroine", "2013")  # Set album
            logmsg.set_album("")  # Clear album context (global)
            logmsg.set_album(None, None, None)  # Also clears
        """
        if not artist or artist == "" or not album or album == "":
            # Clear album context
            self.current_album_info = None
            self.current_album_label = None
        else:
            self.current_album_info = (artist, album, year)
            self.current_album_label = album_label_from_tags(artist, album, year or "")
    
    def set_item(self, item_id: Optional[str] = None) -> None:
        """
        Set the current item context.
        All subsequent logs with item_id will use this item for counting.
        
        Args:
            item_id: Item identifier (e.g., file path, song number) or None/"" to clear
        
        Examples:
            logmsg.set_item(str(src_path))  # Set current item
            logmsg.set_item("")  # Clear item context
        """
        if not item_id or item_id == "":
            self.current_item_id = None
        else:
            self.current_item_id = item_id
    
    def push_item(self, item_id: str) -> None:
        """
        Push an item onto an item stack (for nested item processing).
        Currently same as set_item, but allows future expansion.
        
        Args:
            item_id: Item identifier
        """
        self.current_item_id = item_id
    
    def pop_item(self) -> None:
        """
        Pop an item from the item stack.
        Currently clears item, but allows future expansion.
        """
        self.current_item_id = None
    
    def set_header(
        self, 
        msg: str,
        message_template: Optional[str] = None,
        count_placeholder: str = "%count%"
    ) -> None:
        """
        Set/replace the current header at the current level.
        Shortcut for: pop_header() (if exists) + push_header() at same level.
        
        Use for headers like "Step 1: Process downloads (%count% albums)"
        
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
        
        Examples:
            set_header("H1") -> detail: "H1", summary: "H1"
            set_header("H1", "H1 .. %count%") -> detail: "H1", summary: "H1 .. 2"
            set_header("H1", "%msg% (count = %count%)") -> detail: "H1", summary: "H1 (count = 2)"
        
        Note: %item% placeholder is only for detail logs (info/warn/error), not headers.
        """
        # Shortcut: pop current header (if exists) then push new one at same level
        # Pop current header if exists (but don't write summary - we're replacing it)
        if self.header_stack:
            self.header_stack.pop()
        
        # Use msg as template if message_template not provided
        template = message_template if message_template is not None else msg
        
        # Push new header at same level (or level 0 if was empty)
        # push_header will handle %msg% replacement and immediate replacements
        key = self.push_header(None, template, msg, count_placeholder, None)
        return key
    
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
        category: Optional[str], 
        message_template: str, 
        msg: str,
        count_placeholder: str = "%count%",
        album_label: Optional[str] = None
    ) -> str:
        """
        Push a nested header onto the stack (increases level).
        Formats immediate replacements using any {var} placeholders but leaves %count% for later.
        Returns a key that must be used when popping.
        
        Args:
            category: Header category (e.g., "DOWNLOAD", "UPDATE"), or None
            message_template: Header message template for summary log with placeholders:
                - %msg% for template replacement (replaced with msg parameter)
                - %count% for deferred count replacement (replaced when header written to summary)
                - {var} for immediate replacement from album context (e.g., {artist}, {album}, {year})
            msg: Message for detail log. Replaces %msg% in template.
            count_placeholder: Placeholder for count (default: "%count%")
            album_label: Optional album label (uses current album if None)
        
        Examples:
            push_header("DOWNLOAD", "S1", "S1") -> detail: "S1", summary: "S1"
            push_header("DOWNLOAD", "S1 .. %count%", "S1") -> detail: "S1", summary: "S1 .. 2"
            push_header("DOWNLOAD", "%msg% (count = %count%)", "S1") -> detail: "S1", summary: "S1 (count = 2)"
        
        Note: %item% placeholder is only for detail logs (info/warn/error), not headers.
        
        Returns:
            str: Key to use with pop_header() (for sanity check)
        """
        key = str(uuid.uuid4())
        level = len(self.header_stack)
        
        # Replace %msg% in template with msg
        formatted_template = message_template.replace("%msg%", msg)
        formatted_template = formatted_template.replace("%Msg%", msg)  # Case variation
        
        # Format immediate replacements ({vars}) for template
        formatted_template = self._format_immediate_replacements(formatted_template)
        
        # Format immediate replacements for detail message
        formatted_detail = self._format_immediate_replacements(msg)
        
        header = HeaderContext(
            key=key,
            category=category,
            message_template=formatted_template,
            count_placeholder=count_placeholder,
            level=level,
            detail_message=formatted_detail,
            album_label=album_label or self.current_album_label
        )
        self.header_stack.append(header)
        
        # Log header to detail log immediately (without %count% placeholder)
        self._log_header_to_detail(header)
        
        return key
    
    def pop_header(self, key: str) -> None:
        """
        Pop a header from the stack (decreases level).
        Propagates count to parent, writes to summary if should_log() is True.
        
        Args:
            key: The key returned from push_header() (sanity check)
        
        Raises:
            ValueError: If key doesn't match the top of the stack
        """
        if not self.header_stack:
            raise ValueError("Cannot pop header: stack is empty")
        
        header = self.header_stack[-1]
        if header.key != key:
            raise ValueError(f"Header key mismatch: expected {header.key}, got {key}")
        
        # Pop from stack
        self.header_stack.pop()
        
        # Propagate count to parent (if exists)
        # Parent gets +1 for this child header (the child header itself is one item of the parent)
        if self.header_stack:
            parent = self.header_stack[-1]
            parent.child_count += 1
        
        # Write header to summary if it should be logged
        if header.should_log():
            self._write_header_to_summary(header)
    
    def _log_header_to_detail(self, header: HeaderContext) -> None:
        """
        Log a header to the detail log immediately (without %count% placeholder).
        Used when header is first set/pushed.
        """
        # Use detail_message (always set - either from msg parameter or derived from template)
        message = header.detail_message
        
        # Get indentation and prefix based on header level
        indent = self._get_indent_for_level(header.level)
        prefix = self._get_prefix_for_level(header.level)
        
        formatted = f"{indent}{prefix}{message}"
        
        # Write to detail log
        logger.info(formatted)
    
    def _write_header_to_summary(self, header: HeaderContext) -> None:
        """
        Write a header to the summary log (album summary or global).
        Only called if header.should_log() is True.
        """
        # Replace count placeholder with final value
        total = header.total_count()
        message = header.message_template.replace(header.count_placeholder, str(total))
        # Also replace lowercase/uppercase variations
        if header.count_placeholder == "%count%":
            message = message.replace("%Count%", str(total))
        elif header.count_placeholder == "%Count%":
            message = message.replace("%count%", str(total))
        
        if header.album_label:
            # Write to album summary
            add_album_event_label(header.album_label, message)
        else:
            # Global headers: write to detail log for now
            # TODO: Could add to global summary structure if needed
            logger.info(message)
    
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
        level = len(self.header_stack)
        return self._get_indent_for_level(level)
    
    def _get_prefix(self) -> str:
        """Get prefix (chevrons) based on current header stack level."""
        level = len(self.header_stack)
        return self._get_prefix_for_level(level)
    
    
    def _log_detail(
        self,
        message: str,
        level: str,
        album: Optional[str] = None,
        item: Optional[str] = None
    ) -> None:
        """
        Internal method to log a detail message at the specified level.
        Shared logic for info, warn, and error methods.
        
        Args:
            message: Message to log (can contain %item% placeholder)
            level: Log level ("info", "warn", or "error")
            album: Optional album label (overrides current album context, "" = global)
            item: Optional item identifier (overrides current item context, "" = no item)
        """
        # Determine album and item to use (parameter overrides context)
        use_album = album if album is not None else self.current_album_label
        if album == "":  # Explicitly clear
            use_album = None
        
        use_item = item if item is not None else self.current_item_id
        if item == "":  # Explicitly clear
            use_item = None
        
        # Format message with item placeholder
        formatted_message = message
        if use_item:
            formatted_message = formatted_message.replace("%item%", str(use_item))
            formatted_message = formatted_message.replace("%Item%", str(use_item))  # Case variations
        else:
            formatted_message = formatted_message.replace("%item%", "")
            formatted_message = formatted_message.replace("%Item%", "")
        
        # Build prefix based on level
        level_prefix = {
            "info": "",
            "warn": "[WARN] ",
            "error": "[ERROR] "
        }.get(level, "")
        
        indent = self._get_indent()
        prefix = self._get_prefix()
        formatted = f"{indent}{prefix}{level_prefix}{formatted_message}"
        
        # Write to detail log
        log_method = {
            "info": logger.info,
            "warn": logger.warning,
            "error": logger.error
        }.get(level, logger.info)
        log_method(formatted)
        
        # Add to album/global warnings for warn/error
        if level in ("warn", "error"):
            if use_album:
                add_album_warning_label(use_album, formatted_message)
            else:
                from logging_utils import add_global_warning
                add_global_warning(formatted_message)
        
        # Add to current header's detail messages
        if self.header_stack:
            self.header_stack[-1].detail_messages.append(formatted)
        
        # Automatically count item if item provided (first encounter)
        if use_item:
            self._increment_current_count(use_item)
    
    def info(self, message: str, album: Optional[str] = None, item: Optional[str] = None) -> None:
        """
        Log an info-level detail message.
        Automatically counts item if item is provided (first encounter per header).
        
        Args:
            message: Message to log (can contain %item% placeholder)
            album: Optional album label (overrides current album context, "" = global)
            item: Optional item identifier (overrides current item context, "" = no item)
                 If provided and first encounter, automatically increments count
        """
        self._log_detail(message, "info", album, item)
    
    def warn(self, message: str, album: Optional[str] = None, item: Optional[str] = None) -> None:
        """
        Log a warning-level detail message.
        Automatically counts item if item is provided (first encounter per header).
        
        Args:
            message: Message to log (can contain %item% placeholder)
            album: Optional album label (overrides current album context, "" = global)
            item: Optional item identifier (overrides current item context, "" = no item)
        """
        self._log_detail(message, "warn", album, item)
    
    def error(self, message: str, album: Optional[str] = None, item: Optional[str] = None) -> None:
        """
        Log an error-level detail message.
        Automatically counts item if item is provided (first encounter per header).
        
        Args:
            message: Message to log (can contain %item% placeholder)
            album: Optional album label (overrides current album context, "" = global)
            item: Optional item identifier (overrides current item context, "" = no item)
        """
        self._log_detail(message, "error", album, item)
    
    def _increment_current_count(self, item_id: str) -> None:
        """
        Increment count for current header if item_id hasn't been counted yet.
        This is called automatically when logging with an item_id.
        
        Args:
            item_id: Identifier for the item (must be provided)
        """
        if not self.header_stack or not item_id:
            return
        
        header = self.header_stack[-1]
        
        # Check if this item was already counted in this header
        if item_id not in header.counted_items:
            header.count += 1
            header.counted_items.add(item_id)
    
    def clear(self) -> None:
        """Clear the header stack and album context. Useful for testing."""
        self.header_stack.clear()
        self.current_album_label = None
        self.current_album_info = None


# Global singleton instance
logmsg = StructuredLogger()

