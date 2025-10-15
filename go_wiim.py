"""Wiim-only entrypoint: poll a Wiim device and display album art using existing DisplayController.

This mirrors the high-res Sonos flow but is driven by the Wiim device directly.
"""
import asyncio
import logging
import signal
import sys
import time
from io import BytesIO
import urllib.parse

from aiohttp import ClientSession
from PIL import Image, ImageFile

from display_controller import DisplayController, SonosDisplaySetupError
import sonos_settings
import wiim_client
import wiim_upnp
from collections import deque

_LOGGER = logging.getLogger(__name__)
ImageFile.LOAD_TRUNCATED_IMAGES = True


def setup_logging_local():
    fmt = "%(asctime)s %(levelname)7s - %(message)s"
    level = getattr(sonos_settings, 'log_level', 'INFO')
    try:
        level = getattr(logging, level)
    except Exception:
        level = logging.INFO
    logging.basicConfig(format=fmt, level=level)
    logging.getLogger('aiohttp.access').setLevel(logging.WARNING)


async def fetch_image(session, url):
    if not url:
        return None
    try:
        async with session.get(url, timeout=5, ssl=False) as resp:
            if resp.status == 200 and resp.headers.get('content-type', '').startswith('image/'):
                data = await resp.read()
                return Image.open(BytesIO(data))
    except Exception as err:
        _LOGGER.debug('Failed to fetch image %s [%s]', url, err)
    return None


async def main(loop):
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    # Touch configuration
    touch_enabled = getattr(sonos_settings, 'touch_controls', False)
    touch_detail_timeout = getattr(sonos_settings, 'touch_detail_timeout', None)

    # Maintain latest track info for favorite action
    latest_info = {}

    # recent touch timestamps for multi-tap detection
    touch_times = deque()
    touch_window = getattr(sonos_settings, 'touch_tap_window', 0.6)

    def touch_callback():
        """Handle touch taps: single = show details, double = next, triple = favorite."""
        now = time.time()
        touch_times.append(now)
        # drop old taps
        while touch_times and now - touch_times[0] > touch_window:
            touch_times.popleft()
        cnt = len(touch_times)
        _LOGGER.debug('Touch callback: %d taps in window', cnt)
        if cnt >= 3:
            _LOGGER.info('Touch action: favorite')
            touch_times.clear()
            # schedule favorite handler
            loop.create_task(_run_favorite(latest_info))
        elif cnt >= 2:
            _LOGGER.info('Touch action: next track')
            touch_times.clear()
            # schedule an async task that will await and log the result
            async def _do_next():
                if not base or not session:
                    _LOGGER.debug('No base or session for next_track')
                    return
                ok, status, text = await wiim_client.next_track(session, base)
                _LOGGER.debug('next_track result ok=%s status=%s len_text=%d', ok, status, len(text) if text else 0)
                if not ok:
                    _LOGGER.info('Retrying next_track once')
                    ok2, status2, text2 = await wiim_client.next_track(session, base)
                    _LOGGER.debug('next_track retry ok=%s status=%s', ok2, status2)

            loop.create_task(_do_next())
        else:
            _LOGGER.debug('Touch action: show details')
            display.show_album(show_details=True, detail_timeout=touch_detail_timeout or 8)

    async def _run_favorite(info):
        """Run configured favorite backend (script) with artist/title/album."""
        if not info:
            _LOGGER.debug('No track info available for favorite')
            return
        backend = getattr(sonos_settings, 'touch_favorite_backend', 'script')
        if backend == 'script':
            script = getattr(sonos_settings, 'touch_favorite_script', None)
            if not script:
                _LOGGER.debug('No favorite script configured')
                return
            args = [script, info.get('artist') or '', info.get('title') or '', info.get('album') or '']
            try:
                proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                out, err = await proc.communicate()
                _LOGGER.debug('Favorite script finished: %s %s', out, err)
            except Exception as err:
                _LOGGER.debug('Favorite script failed: %s', err)
        else:
            _LOGGER.debug('Favorite backend %s not implemented', backend)

    # Create aiohttp session early so touch handlers can use it immediately
    session = ClientSession()

    try:
        display = DisplayController(loop, sonos_settings.show_details, sonos_settings.show_artist_and_album,
                                    sonos_settings.show_details_timeout, sonos_settings.overlay_text, sonos_settings.show_play_state, False,
                                    touch_controls=touch_enabled, touch_callback=touch_callback, touch_detail_timeout=touch_detail_timeout)
    except SonosDisplaySetupError:
        loop.stop()
        return

    setup_logging_local()

    base_cfg = getattr(sonos_settings, 'wiim_base_url', '')


    # If base isn't configured, attempt discovery/warmup to find devices
    base = base_cfg
    if not base:
        _LOGGER.info('No wiim_base_url configured — attempting auto-discovery')
        try:
            # warmup will probe discovered devices and cache responsive bases
            bases = await wiim_upnp.warmup(session, timeout=2)
            if bases:
                base = bases[0]
                _LOGGER.info('Auto-discovered Wiim device: %s', base)
            else:
                # As a last attempt, try SSDP discover locations
                locs = await wiim_upnp.discover_locations(loop=loop, timeout=2)
                if locs:
                    parsed = urllib.parse.urlparse(locs[0])
                    base = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or ('443' if parsed.scheme=='https' else '80')}"
                    _LOGGER.info('Discovered location via SSDP: %s', base)
        except Exception as err:
            _LOGGER.debug('Auto-discovery failed: %s', err)

    if not base:
        _LOGGER.error('No Wiim device discovered and no wiim_base_url configured — exiting')
        await session.close()
        return

    previous_track = None
    # Cache recent failed artwork lookups to avoid repeated probes (track_id -> timestamp)
    _failed_art_cache = {}
    art_failure_cooldown = getattr(sonos_settings, 'art_failure_cooldown', 30)  # seconds

    # Track consecutive empty metadata responses for current base and rotate if stuck
    base_empty_count = 0
    base_empty_threshold = getattr(sonos_settings, 'base_empty_threshold', 5)

    def stop_handler():
        asyncio.ensure_future(cleanup(loop, session, display))

    for signame in ('SIGINT', 'SIGTERM', 'SIGQUIT'):
        loop.add_signal_handler(getattr(signal, signame), stop_handler)

    try:
        while True:
            info = await wiim_client.get_now_playing(session, base)
            # If the device returned all-None metadata, count it as an empty response for this base
            if isinstance(info, dict) and not any(info.values()):
                base_empty_count += 1
                _LOGGER.debug('Empty metadata from %s (count=%d)', base, base_empty_count)
                if base_empty_count >= base_empty_threshold:
                    _LOGGER.info('Base %s returned empty metadata %d times — rotating candidates', base, base_empty_count)
                    # Try cached bases from wiim_upnp.warmup
                    try:
                        candidates = await wiim_upnp.warmup(session, timeout=2)
                        for c in candidates:
                            if c != base:
                                _LOGGER.info('Switching to candidate base %s', c)
                                base = c
                                base_empty_count = 0
                                break
                        else:
                            # Try SSDP discovery for new locations
                            locs = await wiim_upnp.discover_locations(loop=loop, timeout=2)
                            if locs:
                                parsed = urllib.parse.urlparse(locs[0])
                                new_base = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or ('443' if parsed.scheme=='https' else '80')}"
                                _LOGGER.info('Switching to discovered base %s', new_base)
                                base = new_base
                                base_empty_count = 0
                    except Exception as err:
                        _LOGGER.debug('Error rotating bases: %s', err)
                    # continue to next loop iteration after changing base
                    await asyncio.sleep(0.5)
                    continue
            else:
                base_empty_count = 0
            # publish latest info for touch favorite handling
            try:
                latest_info.clear()
                if isinstance(info, dict):
                    latest_info.update(info)
            except Exception:
                pass
            _LOGGER.debug('Wiim now playing: %s', info)
            state = (info.get('state') or '').lower()
            track_id = f"{info.get('artist') or ''} - {info.get('title') or ''}"

            # Normalize album_art_uri: treat obvious non-URLs as None
            art_uri = info.get('album_art_uri')
            if art_uri and not str(art_uri).startswith(('http://', 'https://')):
                art_uri = None
                info['album_art_uri'] = None

            # If the device reports stopped state, hide the display/backlight
            try:
                if state in ('stop', 'stopped', 'idle'):
                    _LOGGER.info('Wiim reported stop/idle state — hiding display')
                    display.hide_album()
                    await asyncio.sleep(1)
                    continue
            except Exception:
                pass

            # If the device moved to play and the display is currently hidden, force an update
            try:
                if state.startswith('play') and not getattr(display, 'is_showing', False):
                    _LOGGER.info('Wiim moved to play and display is hidden — forcing update')
                    previous_track = None
            except Exception:
                pass
            if track_id != previous_track:
                previous_track = track_id
                pil_image = None

                # Try WiiM-provided album art URI first
                if info.get('album_art_uri'):
                    _LOGGER.debug('Fetching album_art_uri from Wiim: %s', info.get('album_art_uri'))
                    pil_image = await fetch_image(session, info.get('album_art_uri'))

                # Next try wiim_upnp.probing (templates + SSDP fallback)
                if pil_image is None:
                    try:
                        artist = (info.get('artist') or '').strip()
                        title = (info.get('title') or '').strip()
                        # Skip obvious placeholders or extremely short names
                        skip_probe = False
                        if not artist or not title:
                            skip_probe = True
                        low_artist = artist.lower()
                        low_title = title.lower()
                        if any(x in low_artist for x in ('unknow', 'unknown', 'n/a')) or any(x in low_title for x in ('unknow', 'unknown', 'n/a')):
                            skip_probe = True

                        track_key = f"{artist} - {title}"
                        last_fail = _failed_art_cache.get(track_key)
                        if last_fail and (time.time() - last_fail) < art_failure_cooldown:
                            _LOGGER.debug('Skipping art probe for %s (recent failure)', track_key)
                            skip_probe = True

                        if not skip_probe:
                            _LOGGER.debug('Trying wiim_upnp.get_image_data for %s - %s', artist, title)
                            data = await wiim_upnp.get_image_data(session, artist, title)
                            if data:
                                pil_image = Image.open(BytesIO(data))
                            else:
                                _failed_art_cache[track_key] = time.time()
                    except Exception as err:
                        _LOGGER.debug('wiim_upnp.get_image_data failed: %s', err)

                if pil_image is None:
                    _LOGGER.debug('No album art found; using placeholder')
                    pil_image = Image.new('RGB', (720, 720), color='black')

                # create a fake sonos_data-like object for display.update
                class SD:
                    pass
                sd = SD()
                sd.trackname = info.get('title') or ''
                sd.artist = info.get('artist') or ''
                sd.album = info.get('album') or ''
                sd.station = ''
                sd.volume = None
                sd.shuffle = None
                sd.repeat = None
                sd.crossfade = None
                sd.type = 'wiim'

                display.update(None, pil_image, sd)

            await asyncio.sleep(1)
    finally:
        await cleanup(loop, session, display)


async def cleanup(loop, session, display):
    _LOGGER.debug('Cleaning up')
    display.cleanup()
    await session.close()
    loop.stop()


if __name__ == '__main__':
    # Create and set an explicit event loop to avoid the "There is no current event loop" deprecation warning
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.create_task(main(loop))
        loop.run_forever()
    finally:
        loop.close()
