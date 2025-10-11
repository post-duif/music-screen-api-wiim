Local testing helpers
---------------------

Two small mock servers are included for local development without a Raspberry Pi or HyperPixel attached.

1) Mock Sonos HTTP API

- File: `tests/mock_sonos_api.py`
- Runs a simple aiohttp server that serves `/Bedroom/state` (or `/<room>/state`) and `/test.jpg`.
- Usage:

```bash
python3 tests/mock_sonos_api.py --port 8000
```

2) Mock Wiim album art server

- File: `tests/mock_wiim_server.py`
- Serves `/albumart` and `/nowplaying.jpg` with a tiny test image.
- Usage:

```bash
python3 tests/mock_wiim_server.py --port 49152
```

Configuring `sonos_settings.py` for local testing

- Copy `sonos_settings.py.example` to `sonos_settings.py`.
- Set the Sonos HTTP API to point to the mock Sonos server:

```python
sonos_http_api_address = 'localhost'
sonos_http_api_port = '8000'
room_name_for_highres = 'Bedroom'
```

- To enable Wiim mock server for testing, set:

```python
wiim_enabled = True
wiim_base_url = 'http://localhost:49152'
wiim_albumart_url = '/albumart?artist={artist}&track={track}'
```

Then run `python3 go_sonos_highres.py` — the app will use the mocks and display the test image.
