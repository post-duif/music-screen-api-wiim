#!/usr/bin/env python3
"""Minimal mock of node-sonos-http-api for local testing.

Serves a simple /<room>/state JSON payload and an embedded test image at /test.jpg.
Run this locally and point `sonos_settings.py` to host=localhost port=8000 and
`room_name_for_highres` to the desired room (default 'Bedroom').
"""
import asyncio
from aiohttp import web
import argparse
import base64

# tiny 1x1 PNG (white)
PNG_1x1 = base64.b64decode(
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII='
)


async def handle_state(request):
    room = request.match_info.get('room', 'Bedroom')
    # Minimal payload expected by SonosData.refresh
    payload = {
        'playbackState': 'PLAYING',
        'currentTrack': {
            'type': 'track',
            'duration': 240,
            'title': 'Test Track',
            'artist': 'Test Artist',
            'album': 'Test Album',
            'stationName': '',
            'uri': '',
            # Provide a full URL to the local test image
            'albumArtUri': f'http://{request.host}/test.jpg',
            'absoluteAlbumArtUri': f'http://{request.host}/test.jpg',
            'nextTrack': {'absoluteAlbumArtUri': f'http://{request.host}/test.jpg'}
        }
    }
    return web.json_response(payload)


async def handle_image(request):
    return web.Response(body=PNG_1x1, content_type='image/png')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()

    app = web.Application()
    app.router.add_get('/{room}/state', handle_state)
    app.router.add_get('/test.jpg', handle_image)

    web.run_app(app, host='0.0.0.0', port=args.port)


if __name__ == '__main__':
    main()
