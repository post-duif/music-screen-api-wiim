"""Simple Wiim (or generic device) album art fetcher.

This module intentionally avoids heavy UPnP discovery libraries. It provides
an async helper that will attempt to fetch album art from a configurable
HTTP endpoint on the user's Wiim Pro Plus device. The URL can be a full
template (containing {artist} and {track}) or a base URL plus a path template.

Examples in `sonos_settings.py.example` show how to configure the URLs.
"""
import asyncio
import logging
import socket
import time
import urllib.parse
from typing import List

from aiohttp import ClientError
import aiohttp
import json

import sonos_settings

_LOGGER = logging.getLogger(__name__)

# Cached responsive base hosts discovered at startup or during warmup
_CACHED_BASES: List[str] = []


async def warmup(session, timeout=2):
    """Attempt a short discovery/probe pass and cache responsive base hosts.

    This should be called once at startup to make per-track lookups fast.
    It will attempt SSDP discovery and probe discovered hosts for common album-art
    endpoints. Responsive bases are stored in _CACHED_BASES.
    """
    global _CACHED_BASES
    if _CACHED_BASES:
        return _CACHED_BASES

    bases = []
    # If user provided a base in settings, try that first
    cfg_base = getattr(sonos_settings, 'wiim_base_url', '')
    if cfg_base:
        bases.append(cfg_base)

    # Do SSDP discovery (blocking portion in executor)
    try:
        loop = asyncio.get_event_loop()
        locations = await loop.run_in_executor(None, _sync_ssdp_search, 2, 'ssdp:all', 2)
        validated_hosts = []
        for loc in locations:
            try:
                parsed = urllib.parse.urlparse(loc)
                # Normal base_host from LOCATION
                base_candidate = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or ('443' if parsed.scheme=='https' else '80')}"
            except Exception:
                continue

            # Try to validate the LOCATION by fetching the description or root document
            try:
                async with session.get(loc, timeout=3) as resp:
                    text = await resp.text()
                    # Look for manufacturer/device hints in the XML/HTML
                    hint = None
                    lowered = (text or '').lower()
                    if 'linkplay' in lowered or 'wiim' in lowered or 'wii m' in lowered:
                        hint = 'linkplay'
                    if 'httpapi.asp' in lowered or 'getmetainfo' in lowered or 'getplayerstatus' in lowered:
                        hint = hint or 'httpapi'
                    if hint:
                        validated_hosts.append(base_candidate)
                        _LOGGER.debug('Validated SSDP location %s as Wiim candidate (hint=%s)', base_candidate, hint)
            except Exception:
                # ignore individual location fetch failures
                continue

        # Append validated hosts after any configured base
        for h in validated_hosts:
            if h not in bases:
                bases.append(h)
    except Exception:
        _LOGGER.debug('SSDP discovery during warmup failed')

    responsive = []
    for b in bases:
        try:
            # First, try to confirm the device exposes the WiiM HTTP API (getMetaInfo)
            api_ok = await _test_httpapi(session, b, timeout=timeout)
            if api_ok:
                responsive.append(b)
                continue

            # If API check failed, still try common image paths as a fallback
            data = await _try_common_paths(session, b, '', '', timeout=timeout)
            if data:
                responsive.append(b)
        except Exception:
            continue

    _CACHED_BASES = responsive
    _LOGGER.debug('Wiim warmup cached bases: %s', _CACHED_BASES)
    return _CACHED_BASES


async def _test_httpapi(session, base: str, timeout=3) -> bool:
    """Test whether the device at base exposes the /httpapi.asp HTTP API.

    Tries /httpapi.asp?command=getMetaInfo and getPlayerStatus. If the
    current scheme returns 404 or non-JSON, the function will try the
    opposite scheme (http <-> https) before returning False.
    """
    async def try_one(b):
        meta_url = urllib.parse.urljoin(b, '/httpapi.asp?command=getMetaInfo')
        try:
            async with session.get(meta_url, timeout=timeout, ssl=False) as resp:
                text = await resp.text()
                if resp.status == 200:
                    try:
                        json.loads(text)
                        _LOGGER.debug('HTTP API test succeeded at %s', meta_url)
                        return True
                    except Exception:
                        _LOGGER.debug('HTTP API test: JSON decode failed at %s (len=%d)', meta_url, len(text or ''))
                else:
                    _LOGGER.debug('HTTP API test: status %s at %s', resp.status, meta_url)
        except Exception as err:
            _LOGGER.debug('HTTP API test request failed %s [%s]', meta_url, err)
        return False

    # Try a set of candidate base variants: as-given, same host without explicit port, alternate scheme with and without port
    parsed = urllib.parse.urlparse(base)
    candidates = []

    # as-given
    candidates.append(base)

    # host without explicit port (default port for scheme)
    try:
        host_only = f"{parsed.scheme}://{parsed.hostname}"
        candidates.append(host_only)
    except Exception:
        pass

    # alternate scheme (with and without port)
    try:
        alt_scheme = 'https' if parsed.scheme == 'http' else 'http'
        alt_with_port = f"{alt_scheme}://{parsed.hostname}:{parsed.port or ('443' if alt_scheme=='https' else '80')}"
        alt_host_only = f"{alt_scheme}://{parsed.hostname}"
        candidates.append(alt_with_port)
        candidates.append(alt_host_only)
    except Exception:
        pass

    # Deduplicate while preserving order
    seen = set()
    candidates_unique = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            candidates_unique.append(c)

    for c in candidates_unique:
        try:
            ok = await try_one(c)
            if ok:
                # cache the working base for future lookups
                if c not in _CACHED_BASES:
                    _CACHED_BASES.append(c)
                return True
        except Exception:
            continue

    return False


def _sync_ssdp_search(mx=2, st='ssdp:all', timeout=2) -> List[str]:
    """Perform a simple blocking SSDP M-SEARCH and return list of LOCATION URLs.

    This is run in a thread (via run_in_executor) to avoid blocking the async loop.
    """
    MSEARCH_MSG = '\r\n'.join([
        'M-SEARCH * HTTP/1.1',
        'HOST: 239.255.255.250:1900',
        'MAN: "ssdp:discover"',
        f'MX: {mx}',
        f'ST: {st}',
        '',
        '',
    ])

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)

    locations = []
    try:
        sock.sendto(MSEARCH_MSG.encode('utf-8'), ('239.255.255.250', 1900))
        start = time.time()
        while True:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                break
            text = data.decode('utf-8', errors='ignore')
            for line in text.split('\r\n'):
                if line.lower().startswith('location:'):
                    loc = line.split(':', 1)[1].strip()
                    if loc not in locations:
                        locations.append(loc)
            if time.time() - start > timeout:
                break
    finally:
        sock.close()

    return locations


async def discover_locations(loop=None, timeout=2) -> List[str]:
    """Discover UPnP device LOCATION URLs via SSDP (non-blocking wrapper)."""
    if loop is None:
        loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_ssdp_search, 2, 'ssdp:all', timeout)


async def _try_common_paths(session, base: str, artist: str, track: str, timeout=5):
    """Try a list of common album-art endpoint templates on the device base URL."""
    artist_q = urllib.parse.quote_plus((artist or '').strip())
    track_q = urllib.parse.quote_plus((track or '').strip())
    candidates = [
        '/albumart?artist={artist}&track={track}',
        '/albumart.jpg?artist={artist}&track={track}',
        '/nowplaying/albumart?artist={artist}&track={track}',
        '/nowplaying.jpg',
        '/now_playing.jpg',
        '/image.jpg',
        '/AlbumArt?artist={artist}&track={track}',
        '/photo.jpg',
    ]

    for tpl in candidates:
        path = tpl.format(artist=artist_q, track=track_q)
        url = urllib.parse.urljoin(base, path)
        try:
            async with session.get(url, timeout=timeout) as resp:
                content_type = resp.headers.get('content-type', '')
                if content_type.startswith('image/') and resp.status == 200:
                    return await resp.read()
        except ClientError:
            _LOGGER.debug('Wiim candidate %s failed', url)
        except Exception:
            _LOGGER.debug('Wiim candidate %s unexpected error', url)

    return None


async def get_image_data(session, artist, track, timeout=5):
    """Return image bytes fetched from the Wiim device or None.

    Behaviour:
    - If `wiim_enabled` is False -> return None
    - If `wiim_albumart_url` is configured -> use it (relative or absolute)
    - Else if `wiim_base_url` is configured -> try a set of common paths
    - Else -> attempt SSDP discovery and try common paths on discovered hosts
    """
    if not getattr(sonos_settings, 'wiim_enabled', False):
        return None

    artist = (artist or '')
    track = (track or '')

    template = getattr(sonos_settings, 'wiim_albumart_url', '')
    base = getattr(sonos_settings, 'wiim_base_url', '')

    # If explicit template provided, prefer that
    if template:
        if template.startswith('http://') or template.startswith('https://'):
            url = template.format(artist=urllib.parse.quote_plus(artist.strip()), track=urllib.parse.quote_plus(track.strip()))
            try:
                async with session.get(url, timeout=timeout) as resp:
                    content_type = resp.headers.get('content-type', '')
                    if content_type.startswith('image/') and resp.status == 200:
                        return await resp.read()
            except Exception:
                _LOGGER.debug('Wiim explicit URL failed: %s', url)
        else:
            if not base:
                _LOGGER.debug('No wiim_base_url for relative wiim_albumart_url')
            else:
                url = urllib.parse.urljoin(base, template.format(artist=urllib.parse.quote_plus(artist.strip()), track=urllib.parse.quote_plus(track.strip())))
                try:
                    async with session.get(url, timeout=timeout) as resp:
                        content_type = resp.headers.get('content-type', '')
                        if content_type.startswith('image/') and resp.status == 200:
                            return await resp.read()
                except Exception:
                    _LOGGER.debug('Wiim explicit relative URL failed: %s', url)

    # If base is configured, try common paths
    if base:
        data = await _try_common_paths(session, base, artist, track, timeout=timeout)
        if data:
            return data

    # Check cached responsive bases first (fast)
    for b in _CACHED_BASES:
        try:
            data = await _try_common_paths(session, b, artist, track, timeout=timeout)
            if data:
                return data
        except Exception:
            continue

    # As a last resort, attempt SSDP discovery and try common paths on discovered devices
    try:
        loop = asyncio.get_event_loop()
        locations = await loop.run_in_executor(None, _sync_ssdp_search, 2, 'ssdp:all', 2)
        for loc in locations:
            # extract scheme+host from loc
            try:
                parsed = urllib.parse.urlparse(loc)
                base_host = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or ('443' if parsed.scheme=='https' else '80')}"
            except Exception:
                continue
            data = await _try_common_paths(session, base_host, artist, track, timeout=timeout)
            if data:
                # cache it for future lookups
                if base_host not in _CACHED_BASES:
                    _CACHED_BASES.append(base_host)
                return data
    except Exception:
        _LOGGER.debug('Wiim discovery failed')

    return None
