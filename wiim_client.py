"""Minimal async Python client for WiiM HTTP API used by this project.

This client calls the device's `/httpapi.asp?command=getMetaInfo` and
`/httpapi.asp?command=getPlayerStatus` endpoints and returns a small
dictionary with now-playing info.

Usage: await wiim_client.get_now_playing(session, base_url)
base_url may be 'http://ip:port' or just an IP/hostname (https assumed).
"""
import logging
import urllib.parse

from aiohttp import ClientError

_LOGGER = logging.getLogger(__name__)


def _normalise_base(base: str) -> str:
    if not base:
        return ''
    base = base.strip()
    if base.startswith('http://') or base.startswith('https://'):
        return base.rstrip('/')
    # default to https (WiiM devices often use https)
    return f'https://{base.rstrip("/")}'


async def _fetch_json(session, url, timeout=5, ignore_ssl=True):
    try:
        async with session.get(url, timeout=timeout, ssl=False if ignore_ssl else None) as resp:
            return await resp.json()
    except ClientError as err:
        _LOGGER.debug('Wiim JSON fetch failed %s [%s]', url, err)
    except Exception as err:
        _LOGGER.debug('Wiim JSON fetch unexpected %s [%s]', url, err)
    return None


async def get_now_playing(session, base_url) -> dict:
    """Return now playing info dict or {} on error.

    Returned dict keys: artist, title, album, album_art_uri, state
    """
    result = {'artist': None, 'title': None, 'album': None, 'album_art_uri': None, 'state': None}
    if not base_url:
        return result

    base = _normalise_base(base_url)
    if not base:
        return result

    meta_url = urllib.parse.urljoin(base, '/httpapi.asp?command=getMetaInfo')
    status_url = urllib.parse.urljoin(base, '/httpapi.asp?command=getPlayerStatus')

    meta = await _fetch_json(session, meta_url)
    status = await _fetch_json(session, status_url)

    # meta expected to contain metaData.albumArtURI etc.
    try:
        if meta and isinstance(meta, dict):
            md = meta.get('metaData') or meta.get('meta_data') or {}
            if isinstance(md, dict):
                result['artist'] = md.get('artist')
                result['title'] = md.get('title')
                result['album'] = md.get('album')
                # prefer albumArtURI or album_art_uri
                result['album_art_uri'] = md.get('albumArtURI') or md.get('album_art_uri')

        if status and isinstance(status, dict):
            st = status.get('status') or status.get('playbackState') or None
            result['state'] = st

    except Exception as err:
        _LOGGER.debug('Error parsing WiiM response [%s]', err)

    return result
