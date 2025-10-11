## Repository-specific instructions for AI coding agents

This repository displays Sonos / Last.fm track info on Pimoroni displays (e-ink or HyperPixel).
Use these notes to make safe, focused code changes quickly.

1. Big picture
   - Two main modes: e-ink (go_sonos.py) and high-res HyperPixel (go_sonos_highres.py).
   - High-res app is async, event-driven and uses webhooks via `node-sonos-http-api` (see `webhook_handler.py` and `sonos_user_data.py`).
   - Display logic and hardware handling is encapsulated in `display_controller.py` (Tkinter + Backlight). Demastering of track names is in `demaster.py` / `async_demaster.py`.

2. Key files
   - `go_sonos_highres.py` — main async entry point for HyperPixel; calls `DisplayController`, `SonosData`, and `SonosWebhook`.
   - `display_controller.py` — UI and backlight control. Prefer changing layout/placement here for UI changes.
   - `webhook_handler.py` — exposes REST endpoints and accepts webhooks on port 8080.
   - `sonos_user_data.py` — transforms JSON from `node-sonos-http-api` into fields used by the display.
   - `go_sonos.py` — legacy synchronous e-ink loop (simple polling). Edit for e-ink-specific fixes.
   - `sonos_settings.py.example` — authoritative source for runtime configuration and flags. Never hardcode secrets in code; prefer reading from settings.

3. Developer workflows & quick commands
   - Install native packages before pip: `sudo apt install python3-tk python3-pil python3-pil.imagetk`
   - Install Python deps: `pip3 install -r requirements.txt` (project uses asyncio + aiohttp + Pillow etc.)
   - Run high-res app locally: `python3 go_sonos_highres.py`
   - Run e-ink app for debugging: `python3 go_sonos.py <room-name>`
   - Spotify integration requires adding credentials to `sonos_settings.py`; use `spotipy` and test with `spotipy_auth_search_test.py`.

4. Conventions & patterns to follow
   - Async for high-res path: prefer `async/await` and use `ClientSession` passed through objects (see `go_sonos_highres.py` and `sonos_user_data.SonosData`).
   - Network calls should use timeouts and graceful exceptions — follow the existing style in `async_demaster.py` and `sonos_user_data.py`.
   - UI updates happen on the Tkinter mainloop; `DisplayController.update()` performs blocking UI updates — avoid long-running work on the same thread.
   - Feature flags live in `sonos_settings.py` and are read via getattr defaults in code. Use `getattr(sonos_settings, "name", default)` if adding new flags.

5. Integration points and external deps
   - node-sonos-http-api (local) — provides playback state and sends webhooks (webhook URL should point to host:8080/).
   - Demaster API (optional, remote) — used by default unless `demaster_query_cloud` is false; async wrapper in `async_demaster.py`.
   - Spotify (optional) — controlled by `spotify_client_id`/`spotify_client_secret` in settings; `spotipy` must be installed.

6. Safe change checklist (use before committing)
   - Update `sonos_settings.py.example` when adding configuration flags.
   - For async changes: ensure `ClientSession` is used and closed properly; use `cleanup()` patterns in `go_sonos_highres.py`.
   - For UI changes: run `go_sonos_highres.py` locally and verify behaviour; changing fonts/sizes happens in `display_controller.py`.
   - Preserve existing logging patterns (use `_LOGGER`) and respect `log_level` and optional `log_file` from settings.

7. Examples (quick edits)
   - To add a new REST endpoint: modify `webhook_handler.SonosWebhook.listen()` routes and implement handler methods following `get_status` / `set_room` conventions.
   - To add a new image fallback: update `go_sonos_highres.py` in the `redraw()` function where `pil_image` is selected.

If anything above is unclear or you want more detail about tests, packaging, or a specific file, tell me which area to expand and I'll update this guidance.
