"""Wiim-only entrypoint: poll a Wiim device and display album art using existing DisplayController.

This mirrors the high-res Sonos flow but is driven by the Wiim device directly.
"""
import asyncio
import logging
import signal
import sys
import time
from io import BytesIO

from aiohttp import ClientSession
from PIL import Image, ImageFile

from display_controller import DisplayController, SonosDisplaySetupError
import sonos_settings
import wiim_client

_LOGGER = logging.getLogger(__name__)
ImageFile.LOAD_TRUNCATED_IMAGES = True


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

    base = getattr(sonos_settings, 'wiim_base_url', '')
    if not base:
        print('No Wiim device configured in sonos_settings.py (wiim_base_url).')
        return

    session = ClientSession()

    previous_track = None

    def stop_handler():
        asyncio.ensure_future(cleanup(loop, session, display))

    for signame in ('SIGINT', 'SIGTERM', 'SIGQUIT'):
        loop.add_signal_handler(getattr(signal, signame), stop_handler)

    try:
        while True:
            info = await wiim_client.get_now_playing(session, base)
            track_id = f"{info.get('artist') or ''} - {info.get('title') or ''}"
            if track_id != previous_track:
                previous_track = track_id
                # fetch album art
                pil_image = None
                if info.get('album_art_uri'):
                    pil_image = await fetch_image(session, info.get('album_art_uri'))
                if pil_image is None:
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
