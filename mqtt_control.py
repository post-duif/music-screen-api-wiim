"""Simple MQTT control bridge for Home Assistant.

Subscribes to <mqtt_topic_prefix>/command and accepts payloads like:
  start, stop, quit, screen:on, screen:off, brightness:<0-100>

It calls back into the main app via provided callbacks.
"""
import logging
import threading
import time
from typing import Callable, Optional

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)


class MQTTControl:
    def __init__(self, broker, port=1883, user='', password='', topic_prefix='home/music-screen'):
        self.broker = broker
        self.port = port
        self.user = user
        self.password = password
        self.topic_prefix = topic_prefix.rstrip('/')
        self.client = mqtt.Client()
        self._thread = None
        self._stopping = False
        self.on_command = None  # type: Optional[Callable[[str], None]]

        if user:
            self.client.username_pw_set(user, password)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        _LOGGER.info('Connected to MQTT broker %s:%s (rc=%s)', self.broker, self.port, rc)
        topic = f"{self.topic_prefix}/command"
        client.subscribe(topic)
        _LOGGER.info('Subscribed to %s', topic)

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode('utf-8', errors='ignore').strip()
        _LOGGER.debug('MQTT message on %s: %s', msg.topic, payload)
        if self.on_command:
            try:
                self.on_command(payload)
            except Exception as err:
                _LOGGER.exception('MQTT command handler failed: %s', err)

    def start(self):
        self._stopping = False
        self.client.connect(self.broker, port=self.port, keepalive=60)
        # run network loop in background thread
        self._thread = threading.Thread(target=self.client.loop_forever, daemon=True)
        self._thread.start()

    def stop(self):
        try:
            self.client.disconnect()
        except Exception:
            pass
        self._stopping = True
        if self._thread:
            self._thread.join(timeout=1)


def _default_dispatch(cmd, display=None, backlight=None, runner_control=None):
    """Default dispatcher mapping command strings to actions."""
    # runner_control is expected to be a dict-like with 'stop' or 'quit' callables
    if cmd in ('quit', 'stop') and runner_control and runner_control.get('quit'):
        runner_control['quit']()
    elif cmd in ('start', 'resume') and runner_control and runner_control.get('start'):
        runner_control['start']()
    elif cmd in ('pause',):
        if display:
            display.hide_album()
    elif cmd.startswith('screen:'):
        v = cmd.split(':', 1)[1]
        if v in ('on', 'true', '1'):
            if backlight:
                backlight.set_power(True)
        else:
            if backlight:
                backlight.set_power(False)
    elif cmd.startswith('brightness:'):
        try:
            b = int(cmd.split(':', 1)[1])
            if backlight and hasattr(backlight, 'set_brightness'):
                backlight.set_brightness(max(0, min(100, b)))
        except Exception:
            _LOGGER.debug('Invalid brightness payload: %s', cmd)


# Convenience factory
def make_mqtt(broker, port, user, password, topic_prefix, display=None, backlight=None, runner_control=None):
    obj = MQTTControl(broker, port, user, password, topic_prefix)
    obj.on_command = lambda c: _default_dispatch(c, display=display, backlight=backlight, runner_control=runner_control)
    return obj
