"""
ROON library refresh module.

Handles refreshing ROON's music library after sync operations.
ROON needs to rescan its library to pick up new/changed files.

Uses ROCK server REST API to restart ROON software remotely.
"""
import logging

import requests

from config import (
    ENABLE_ROON_REFRESH,
    ROON_REFRESH_METHOD,
    ROCK_SERVER_IP,
    ROCK_API_ENDPOINT,
    ROCK_API_METHOD,
    ROCK_API_HEADERS,
    ROCK_API_DATA,
    ROCK_API_COOKIES,
)

logger = logging.getLogger("library_sync")


def refresh_roon_library(dry_run: bool = False) -> bool:
    """
    Refresh ROON's music library after sync operations.
    
    Args:
        dry_run: If True, log what would be done but don't actually refresh
        
    Returns:
        True if refresh was successful or skipped, False if refresh failed
    """
    from structured_logging import logmsg
    
    if not ENABLE_ROON_REFRESH:
        logmsg.verbose("ROON refresh disabled in configuration - skipping")
        logger.info("[ROON REFRESH] Disabled in configuration - skipping")
        return True
    
    if ROON_REFRESH_METHOD == "none":
        logmsg.verbose("ROON refresh method set to 'none' - skipping")
        logger.info("[ROON REFRESH] Method set to 'none' - skipping")
        return True
    
    logger.info(f"[ROON REFRESH] Attempting to refresh ROON library using method: {ROON_REFRESH_METHOD}")
    
    try:
        if ROON_REFRESH_METHOD == "rock_api":
            success = _restart_via_rock_api(dry_run)
            if success:
                if dry_run:
                    logmsg.info("Would refresh ROON library via ROCK API")
                else:
                    logmsg.info("Refreshed ROON library via ROCK API")
            else:
                logmsg.warn("Failed to refresh ROON library via ROCK API")
            return success
        else:
            logmsg.warn("Unknown ROON refresh method: {method} - skipping. Use 'rock_api' or 'none'.", method=ROON_REFRESH_METHOD)
            logger.warning(f"[ROON REFRESH] Unknown method: {ROON_REFRESH_METHOD} - skipping. Use 'rock_api' or 'none'.")
            return False
    except Exception as e:
        logmsg.error("Error during ROON refresh: {error}", error=str(e))
        logger.error(f"[ROON REFRESH] Error during refresh: {e}")
        return False


def _restart_via_rock_api(dry_run: bool = False) -> bool:
    """
    Restart ROON software via ROCK server REST API.
    
    Sends a POST request to the ROCK server's restart endpoint to restart
    the ROON software, causing it to rescan the library for new/changed files.
    """
    rock_server = ROCK_SERVER_IP
    
    # Build the full URL
    endpoint = ROCK_API_ENDPOINT.lstrip('/')  # Remove leading slash if present
    url = f"http://{rock_server}/{endpoint}"
    
    # Prepare request kwargs
    method = ROCK_API_METHOD.upper()
    kwargs = {
        "timeout": 10,
    }
    
    # Add headers if provided
    if ROCK_API_HEADERS:
        kwargs["headers"] = ROCK_API_HEADERS
    
    # Add cookies if provided
    if ROCK_API_COOKIES:
        kwargs["cookies"] = ROCK_API_COOKIES
    
    # Add data/body for POST requests
    if method == "POST" and ROCK_API_DATA is not None:
        # Check if headers indicate JSON, otherwise use form data
        content_type = ROCK_API_HEADERS.get("Content-Type", "").lower() if ROCK_API_HEADERS else ""
        if "json" in content_type:
            kwargs["json"] = ROCK_API_DATA
        else:
            kwargs["data"] = ROCK_API_DATA
    
    logger.info(f"[ROON REFRESH] Restarting ROON software via ROCK API: {method} {url}")
    if ROCK_API_HEADERS:
        logger.debug(f"[ROON REFRESH] Headers: {ROCK_API_HEADERS}")
    if ROCK_API_DATA is not None:
        logger.debug(f"[ROON REFRESH] Data: {ROCK_API_DATA}")
    
    if dry_run:
        logger.info(f"[ROON REFRESH] DRY RUN: Would call ROCK API: {method} {url}")
        if ROCK_API_HEADERS:
            logger.info(f"[ROON REFRESH] DRY RUN: With headers: {ROCK_API_HEADERS}")
        if ROCK_API_DATA is not None:
            logger.info(f"[ROON REFRESH] DRY RUN: With data: {ROCK_API_DATA}")
        return True
    
    try:
        # Make the request using the configured method
        if method == "GET":
            r = requests.get(url, **kwargs)
        elif method == "POST":
            r = requests.post(url, **kwargs)
        else:
            logger.error(f"[ROON REFRESH] Unsupported HTTP method: {method}. Use GET or POST.")
            return False
        
        r.raise_for_status()
        logger.info(f"[ROON REFRESH] Successfully sent restart command to ROCK server (status: {r.status_code})")
        if r.text:
            logger.debug(f"[ROON REFRESH] Response: {r.text[:200]}")  # Log first 200 chars of response
        return True
        
    except requests.exceptions.Timeout:
        logger.error(f"[ROON REFRESH] Timeout connecting to ROCK server at {rock_server}")
        return False
    except requests.exceptions.ConnectionError:
        logger.error(f"[ROON REFRESH] Could not connect to ROCK server at {rock_server} - is it reachable?")
        return False
    except requests.exceptions.HTTPError as e:
        logger.error(f"[ROON REFRESH] ROCK API returned error: {e.response.status_code} {e.response.reason}")
        if e.response.text:
            logger.debug(f"[ROON REFRESH] Error response: {e.response.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"[ROON REFRESH] Exception while calling ROCK API: {e}")
        logger.exception("Full traceback:")
        return False



