"""Opt-in media-server integration: tell Jellyfin the library changed.

ShrinkRay is the janitor, not the butler: it never serves media and never
touches the media server's databases. The whole integration is one
polite tap on the shoulder - after successful encodes, ask Jellyfin to
re-scan its library so the swapped file shows up with fresh metadata.

Design constraints:
- opt-in and OFF by default (nothing happens without explicit config)
- the token NEVER lives in config.json (it is a tracked file): config
  names an environment variable, the token comes from the environment
- fire-and-forget: a notification failure is logged, never raised - a
  media server being down must not fail an encode
"""

import os
from pathlib import Path

from shrinkray.logger import log, log_warning

DEFAULT_TOKEN_ENV = "SHRINKRAY_JELLYFIN_TOKEN"
DEFAULT_TIMEOUT = 5.0


def jellyfin_settings(config) -> dict | None:
    """Reads the Jellyfin settings from a config (Conf) object.

    Args:
        config: The config object (reads the integrations.jellyfin dict).

    Returns:
        dict | None: {"url": ..., "token": ...} when integration is
            configured AND usable (token present in the environment);
            None otherwise (disabled, unconfigured, or token missing).
    """
    integrations = getattr(config, "integrations", None) or {}
    jellyfin = integrations.get("jellyfin") or {}
    url = (jellyfin.get("url") or "").strip()
    if not url:
        return None
    env_var = (jellyfin.get("token_env") or DEFAULT_TOKEN_ENV).strip()
    token = os.environ.get(env_var, "").strip()
    if not token:
        log_warning(
            f"Jellyfin integration configured, but environment variable "
            f"'{env_var}' is not set => refresh notifications are skipped."
        )
        return None
    return {"url": url.rstrip("/"), "token": token}


def notify_jellyfin(config, timeout: float = DEFAULT_TIMEOUT) -> bool:
    """Asks Jellyfin to refresh its library (fire-and-forget).

    Posts to {url}/Library/Refresh with the token from the configured
    environment variable. Never raises: any failure (unreachable server,
    bad token, timeout) is logged and reported as False. Safe to call
    unconditionally - without configuration this is a no-op.

    Args:
        config: The config object.
        timeout (float): Request timeout in seconds (short on purpose).

    Returns:
        bool: True when Jellyfin acknowledged the refresh.
    """
    settings = jellyfin_settings(config)
    if settings is None:
        return False
    try:
        # stdlib only: one POST does not justify a dependency
        import urllib.request

        request = urllib.request.Request(
            f"{settings['url']}/Library/Refresh",
            method="POST",
            headers={"X-Emby-Token": settings["token"]},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            ok = 200 <= response.status < 300
    except Exception as e:  # fire-and-forget, by contract
        log_warning(f"Jellyfin refresh failed (encode continues unaffected): {e}")
        return False
    if ok:
        log("Jellyfin library refresh requested.")
    else:
        log_warning("Jellyfin refresh request returned a non-2xx status.")
    return ok


def notify_after_encodes(config, movies: list[Path]) -> int:
    """Notifies the media server once after a batch of successful encodes.

    Args:
        config: The config object.
        movies (list[Path]): The movies whose encodes finished.

    Returns:
        int: The number of movies that were successfully encoded (the
            caller uses this to decide whether a refresh is warranted).
    """
    if not movies:
        return 0
    notified = notify_jellyfin(config)
    if notified:
        log(f"Media server notified about {len(movies)} re-encoded movie(s).")
    return len(movies)
