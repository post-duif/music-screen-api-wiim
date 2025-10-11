#!/usr/bin/env python3
"""Simple mock Wiim album art HTTP server for local testing.

Usage: python3 tests/mock_wiim_server.py --port 49152
It will serve /albumart?artist=...&track=... and /nowplaying.jpg
"""
from aiohttp import web
import argparse
import base64

# tiny 1x1 PNG (blue)
PNG_1x1 = base64.b64decode(
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII='
)


async def albumart(request):
    # we ignore artist/track for this mock and always return the test image
    return web.Response(body=PNG_1x1, content_type='image/png')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=49152)
    args = parser.parse_args()

    app = web.Application()
    app.router.add_get('/albumart', albumart)
    app.router.add_get('/nowplaying.jpg', albumart)

    web.run_app(app, host='0.0.0.0', port=args.port)


if __name__ == '__main__':
    main()
