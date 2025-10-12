"""Implementation of the DisplayController class."""
import logging
import os
import tkinter as tk
from tkinter import Y, font as tkFont

from PIL import Image, ImageTk

from hyperpixel_backlight import Backlight

_LOGGER = logging.getLogger(__name__)

# Choose a resampling filter compatible with different Pillow versions.
# Pillow 9.1+ exposes Image.Resampling.LANCZOS, older versions expose Image.LANCZOS
# and very old versions used Image.ANTIALIAS. Fall back to NEAREST if none found.
try:
    RESAMPLE_FILTER = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE_FILTER = getattr(Image, 'LANCZOS', getattr(Image, 'ANTIALIAS', Image.NEAREST))

class SonosDisplaySetupError(Exception):
    """Error connecting to Sonos display."""

class DisplayController:  # pylint: disable=too-many-instance-attributes
    """Controller to handle the display hardware and GUI interface."""

    def __init__(self, loop, show_details, show_artist_and_album, show_details_timeout, overlay_text, show_play_state, show_spotify_code, touch_controls=False, touch_callback=None, touch_detail_timeout=None):
        """Initialize the display controller."""

        self.SCREEN_W = 720
        self.SCREEN_H = 720
        self.THUMB_W = 0
        self.THUMB_H = 0

        self.loop = loop
        self.show_details = show_details
        self.show_artist_and_album = show_artist_and_album
        self.show_details_timeout = show_details_timeout
        self.overlay_text = overlay_text
        self.show_play_state = show_play_state
        self.show_spotify_code = show_spotify_code

        self.album_image = None
        self.thumb_image = None
        self.code_image = None
        self.label_track = None
        self.label_detail = None
        self.label_play_state = None
        self.label_spotify_code = None
        self.label_spotify_code_detail = None
        self.track_font = None
        self.detail_font = None
        self.timeout_future = None
        self.is_showing = False

        self.backlight = Backlight()
        self.touch_controls = touch_controls
        self.touch_callback = touch_callback
        self.touch_detail_timeout = touch_detail_timeout or 8

        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.root = None

        if not self.root:
            os.environ["DISPLAY"] = ":0"
            try:
                self.root = tk.Tk()
            except tk.TclError as error:
                _LOGGER.error("Cannot access display: %s", error)
                raise SonosDisplaySetupError

        self.root.geometry(f"{self.SCREEN_W}x{self.SCREEN_H}")

        self.album_frame = tk.Frame(
            self.root, bg="black", width=self.SCREEN_W, height=self.SCREEN_H
        )
        self.album_frame.grid(row=0, column=0, sticky="news")

        self.detail_frame = tk.Frame(
            self.root, bg="black", width=self.SCREEN_W, height=self.SCREEN_H
        )
        self.detail_frame.grid(row=0, column=0, sticky="news")

        self.curtain_frame = tk.Frame(
            self.root, bg="black", width=self.SCREEN_W, height=self.SCREEN_H
        )
        self.curtain_frame.grid(row=0, column=0, sticky="news")

        self.track_name = tk.StringVar()
        self.detail_text = tk.StringVar()
        self.play_state_text = tk.StringVar()

        self.detail_font = tkFont.Font(family="consolas", size=14)
        self.play_state_font = tkFont.Font(family="consolas", size=14)

        self.label_albumart = tk.Label(
            self.album_frame,
            image=None,
            borderwidth=0,
            highlightthickness=0,
            fg="white",
            bg="black",
        )
        self.label_albumart.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        self.label_albumart_detail = tk.Label(
            self.detail_frame,
            image=None,
            borderwidth=0,
            highlightthickness=0,
            fg="white",
            bg="black",
        )
        self.label_track = tk.Label(
            self.detail_frame,
            textvariable=self.track_name,
            fg="white",
            bg="black",
            wraplength=600,
            justify="center",
        )
        self.label_detail = tk.Label(
            self.detail_frame,
            textvariable=self.detail_text,
            font=self.detail_font,
            fg="white",
            bg="black",
            wraplength=600,
            justify="center",
        )
        self.label_play_state = tk.Label(
            self.detail_frame,
            textvariable=self.play_state_text,
            fg="white",
            bg="black",
            wraplength=700,
            justify="center",
        )
        self.label_spotify_code = tk.Label(
            self.album_frame,
            image=None,
            borderwidth=0,
            highlightthickness=0,
            fg="white",
            bg="#368A7D",
        )
        self.label_spotify_code.place(relx=0.75, y=40, anchor=tk.N)
        
        self.label_spotify_code_detail = tk.Label(
            self.detail_frame,
            image=None,
            borderwidth=0,
            highlightthickness=0,
            fg="white",
            bg="#368A7D",
        )
        self.label_spotify_code_detail.place(relx=0.75, y=40, anchor=tk.N)

        self.album_frame.grid_propagate(False)
        self.detail_frame.grid_propagate(False)

        self.root.attributes("-fullscreen", True)
        # Bind touch/click if enabled
        if self.touch_controls:
            try:
                self.root.bind('<Button-1>', self._on_touch)
            except Exception:
                _LOGGER.debug('Failed to bind touch event')

        self.root.update()

    def _on_touch(self, event):
        """Internal handler for touch/click events."""
        _LOGGER.debug('Touch event at %s,%s', event.x, event.y)
        # Trigger external callback if provided
        try:
            if self.touch_callback:
                self.touch_callback()
        except Exception as err:
            _LOGGER.debug('Touch callback error: %s', err)
        # Default behaviour: show details temporarily
        self.show_album(show_details=True, detail_timeout=self.touch_detail_timeout)

    def show_album(self, show_details=None, detail_timeout=None):
        """Show album with optional detail display and timeout."""
        def handle_timeout():
            self.timeout_future = None
            self.show_album(show_details=False)

        if show_details is None and detail_timeout is None:
            self.curtain_frame.lower()
        elif show_details:
            self.detail_frame.lift()
            if detail_timeout:
                if self.timeout_future:
                    self.timeout_future.cancel()
                self.timeout_future = self.loop.call_later(detail_timeout, handle_timeout)
        else:
            self.album_frame.lift()

        self.is_showing = True
        self.root.update()
        self.backlight.set_power(True)

    def hide_album(self):
        """Hide album if showing."""
        if self.timeout_future:
            self.timeout_future.cancel()
            self.timeout_future = None
            self.show_album(show_details=False)

        self.is_showing = False
        self.backlight.set_power(False)
        self.curtain_frame.lift()
        self.root.update()
        self.label_spotify_code.destroy()
        self.label_spotify_code_detail.destroy()

    def update(self, code_image, image, sonos_data):
        """Update displayed image and text."""

        def resize_image(image, length):
            """Resizes the image, assumes square image."""
            # Use the compatibility RESAMPLE_FILTER selected above.
            image = image.resize((length, length), resample=RESAMPLE_FILTER)
            return ImageTk.PhotoImage(image)

        if code_image != None:
           code_image = ImageTk.PhotoImage(code_image)

        display_trackname = sonos_data.trackname or sonos_data.station

        detail_text = ""
        play_state_text = ""

        if self.show_artist_and_album:
            detail_prefix = None
            detail_suffix = sonos_data.album or None

            if sonos_data.artist != display_trackname:
                detail_prefix = sonos_data.artist

            detail_text = " • ".join(filter(None, [detail_prefix, detail_suffix]))

        if self.show_play_state:
            play_state_volume = sonos_data.volume or None
            play_state_shuffle = sonos_data.shuffle or None
            play_state_repeat = sonos_data.repeat or None
            play_state_crossfade = sonos_data.crossfade or None

            play_state_volume_text = "Volume: " + str(play_state_volume)

            play_state_shuffle_text = "Shuffle: " + str(play_state_shuffle).capitalize()

            play_state_repeat_text = "Repeat: " + str(play_state_repeat).capitalize()

            play_state_crossfade_text = "Crossfade: " + str(play_state_crossfade).capitalize()

            play_state_text = " • ".join(filter(None, [play_state_volume_text, play_state_shuffle_text, play_state_repeat_text, play_state_crossfade_text]))

        if self.show_artist_and_album:
            if len(display_trackname) > 27:
                if len(detail_text) > 54:
                    self.THUMB_H = 565
                    self.THUMB_W = 565
                else:
                    self.THUMB_H = 590
                    self.THUMB_W = 590
                if detail_text == "":
                    self.track_font = tkFont.Font(family="consolas", size=27)
                else:
                    self.track_font = tkFont.Font(family="consolas", size=22)
            else:
                if len(detail_text) > 54:
                    self.THUMB_H = 600
                    self.THUMB_W = 600
                else:
                    self.THUMB_H = 620
                    self.THUMB_W = 620
                if detail_text == "":
                    self.track_font = tkFont.Font(family="consolas", size=37)
                    self.THUMB_H = self.THUMB_H + 20
                    self.THUMB_W = self.THUMB_W + 20
                else:
                    self.track_font = tkFont.Font(family="consolas", size=27)

            if len(display_trackname) > 27 and len(display_trackname) < 34:
                self.THUMB_H = self.THUMB_H + 40
                self.THUMB_W = self.THUMB_W + 40
            
        else:
            if len(display_trackname) > 22:
                self.THUMB_H = 610
                self.THUMB_W = 610
                self.track_font = tkFont.Font(family="consolas", size=27)
            else:
                self.THUMB_H = 640
                self.THUMB_W = 640
                self.track_font = tkFont.Font(family="consolas", size=37)

            if len(display_trackname) > 22 and len(display_trackname) < 35:
                self.THUMB_H = self.THUMB_H + 40
                self.THUMB_W = self.THUMB_W + 40

        # Store the images as attributes to preserve scope for Tk
        self.album_image = resize_image(image, self.SCREEN_W)
        if self.overlay_text:
            self.thumb_image = resize_image(image, self.SCREEN_W)
            self.label_albumart_detail.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        else:
            self.thumb_image = resize_image(image, self.THUMB_W)
            self.label_albumart_detail.place(relx=0.5, y=self.THUMB_H / 2, anchor=tk.CENTER)

        self.label_track.place(relx=0.5, y=self.THUMB_H + 10, anchor=tk.N)

        if detail_text == "" or not self.show_artist_and_album:
            self.label_detail.destroy()
        else:
            if self.label_detail.winfo_exists() == 0:
                self.label_detail = tk.Label(
                    self.detail_frame,
                    textvariable=self.detail_text,
                    font=self.detail_font,
                    fg="white",
                    bg="black",
                    wraplength=600,
                    justify="center",
                )
            self.label_detail.place(relx=0.5, y=self.SCREEN_H - 10, anchor=tk.S)
            self.label_detail.configure(font=self.detail_font)

        if not self.show_play_state:
            self.label_play_state.destroy()
        else:
            if self.label_play_state.winfo_exists() == 0:
                self.label_play_state = tk.Label(
                    self.detail_frame,
                    textvariable=self.play_state_text,
                    font=self.play_state_font,
                    fg="white",
                    bg="black",
                    wraplength=700,
                    justify="center",
                )
            self.label_play_state.place(relx=0.5, y= 10, anchor=tk.N)
            self.label_play_state.configure(font=self.play_state_font)

        if not self.show_spotify_code or code_image == None  or detail_text == "":
            self.label_spotify_code.destroy()
            self.label_spotify_code_detail.destroy()
        else:
            if self.label_spotify_code.winfo_exists() == 0:
                self.label_spotify_code = tk.Label(
                    self.album_frame,
                    image=None,
                    borderwidth=0,
                    highlightthickness=0,
                    fg="white",
                    bg="#368A7D",
                )
                self.label_spotify_code.place(relx=0.75, y=40, anchor=tk.N)
            if code_image != None:
                self.label_spotify_code.configure(image=code_image)
                
            if self.label_spotify_code_detail.winfo_exists() == 0:
                self.label_spotify_code_detail = tk.Label(
                    self.detail_frame,
                    image=None,
                    borderwidth=0,
                    highlightthickness=0,
                    fg="white",
                    bg="#368A7D",
                )
                self.label_spotify_code_detail.place(relx=0.75, y=40, anchor=tk.N)
            if code_image != None:
                self.label_spotify_code_detail.configure(image=code_image) 

        self.label_albumart.configure(image=self.album_image)
        self.label_albumart_detail.configure(image=self.thumb_image)
        self.label_track.configure(font=self.track_font)
        self.track_name.set(display_trackname)
        self.detail_text.set(detail_text)
        self.play_state_text.set(play_state_text)
        
        self.root.update_idletasks()
        self.show_album(self.show_details, self.show_details_timeout)

    def cleanup(self):
        """Run cleanup actions."""
        self.backlight.cleanup()

