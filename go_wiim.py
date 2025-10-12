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
    try:
        display = DisplayController(loop, sonos_settings.show_details, sonos_settings.show_artist_and_album,
                                    sonos_settings.show_details_timeout, sonos_settings.overlay_text, sonos_settings.show_play_state, False)
    except SonosDisplaySetupError:
        loop.stop()
        return

    setup_logging_local()

    base_cfg = getattr(sonos_settings, 'wiim_base_url', '')

    session = ClientSession()

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

    def stop_handler():
        asyncio.ensure_future(cleanup(loop, session, display))

    for signame in ('SIGINT', 'SIGTERM', 'SIGQUIT'):
        loop.add_signal_handler(getattr(signal, signame), stop_handler)

    try:
        while True:
            info = await wiim_client.get_now_playing(session, base)
            _LOGGER.debug('Wiim now playing: %s', info)
            track_id = f"{info.get('artist') or ''} - {info.get('title') or ''}"
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
                        _LOGGER.debug('Trying wiim_upnp.get_image_data for %s - %s', info.get('artist'), info.get('title'))
                        data = await wiim_upnp.get_image_data(session, info.get('artist'), info.get('title'))
                        if data:
                            pil_image = Image.open(BytesIO(data))
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
