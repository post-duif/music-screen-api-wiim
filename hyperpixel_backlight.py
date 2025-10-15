"""
Support class to control the backlight power on a HyperPixel display.
"""
import logging
import os

_LOGGER = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


BACKLIGHT_PIN = 19


class Backlight():

    def __init__(self, initial_value=False):
        """Initialize the backlight instance."""
        self.power = None

        if not GPIO:
            self.active = False
            _LOGGER.error("Backlight control not available, please ensure RPi.GPIO python3 package is installed")
            return

        GPIO.setwarnings(False)
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(BACKLIGHT_PIN, GPIO.OUT)
            self.active = True
        except RuntimeError:
            self.active = False
            username = os.environ.get('USER')
            _LOGGER.error("Backlight control not available, please ensure '%s' is part of group 'gpio'.", username)
            _LOGGER.error("  To add user to group: `sudo gpasswd -a %s gpio`", username)
        else:
            # Try to initialize PWM for brightness control
            try:
                self.pwm = GPIO.PWM(BACKLIGHT_PIN, 1000)
                self.pwm.start(100 if initial_value else 0)
                self._brightness = 100 if initial_value else 0
            except Exception:
                self.pwm = None
                self._brightness = 100 if initial_value else 0
            self.set_power(initial_value)

    def set_power(self, new_state):
        """Control the backlight power of the HyperPixel display."""
        if not self.active:
            return

        if new_state is False and self.power:
            _LOGGER.debug("Going idle, turning backlight off")
        self.power = new_state
        try:
            if getattr(self, 'pwm', None) is not None:
                if new_state:
                    self.pwm.ChangeDutyCycle(self._brightness)
                else:
                    self.pwm.ChangeDutyCycle(0)
            else:
                GPIO.output(BACKLIGHT_PIN, new_state)
        except RuntimeError as err:
            _LOGGER.error("GPIO.output failed: %s", err)
            # Disable further attempts to touch GPIO to avoid repeated errors
            self.active = False

    def set_brightness(self, value: int):
        """Set brightness 0..100 when PWM available."""
        if not self.active:
            return
        value = max(0, min(100, int(value)))
        self._brightness = value
        try:
            if getattr(self, 'pwm', None) is not None:
                self.pwm.ChangeDutyCycle(value)
            else:
                GPIO.output(BACKLIGHT_PIN, value >= 50)
        except Exception as err:
            _LOGGER.debug('set_brightness failed: %s', err)

    def cleanup(self):
        """Return the GPIO setup to initial state."""
        if self.active:
            try:
                GPIO.output(BACKLIGHT_PIN, True)
            except RuntimeError as err:
                _LOGGER.debug("GPIO cleanup output failed: %s", err)
            try:
                GPIO.cleanup()
            except RuntimeError as err:
                _LOGGER.debug("GPIO.cleanup failed: %s", err)
