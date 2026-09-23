import copy

from arlo.messages import Message
import arlo.messages
from arlo.device import Device

DEVICE_PREFIXES = [
    'VMC',
    'VML',
    'ABC',
    'FB'
]


class Camera(Device):
    @property
    def port(self):
        return 4000

    def _is_floodlight(self):
        return self.model_number.startswith('FB1001')

    def _get_quality_message_templates(self):
        if self._is_floodlight():
            return {
                "low": (arlo.messages.RA_PARAMS_FLOODLIGHT, arlo.messages.REGISTER_SET_LOW_QUALITY_FLOODLIGHT),
                "medium": (arlo.messages.RA_PARAMS_FLOODLIGHT, arlo.messages.REGISTER_SET_MEDIUM_QUALITY_FLOODLIGHT),
                "high": (arlo.messages.RA_PARAMS_FLOODLIGHT, arlo.messages.REGISTER_SET_HIGH_QUALITY_FLOODLIGHT),
                "subscription": (arlo.messages.RA_PARAMS_FLOODLIGHT, arlo.messages.REGISTER_SET_HIGH_QUALITY_FLOODLIGHT),
                "insane": (arlo.messages.RA_PARAMS_FLOODLIGHT, arlo.messages.REGISTER_SET_HIGH_QUALITY_FLOODLIGHT),
            }

        return {
            "low": (arlo.messages.RA_PARAMS_LOW_QUALITY, arlo.messages.REGISTER_SET_LOW_QUALITY),
            "medium": (arlo.messages.RA_PARAMS_MEDIUM_QUALITY, arlo.messages.REGISTER_SET_MEDIUM_QUALITY),
            "high": (arlo.messages.RA_PARAMS_HIGH_QUALITY, arlo.messages.REGISTER_SET_HIGH_QUALITY),
            "subscription": (arlo.messages.RA_PARAMS_SUBSCRIPTION_QUALITY, arlo.messages.REGISTER_SET_SUBSCRIPTION_QUALITY),
            "insane": (arlo.messages.RA_PARAMS_INSANE_QUALITY, arlo.messages.REGISTER_SET_INSANE_QUALITY),
        }

    def _get_quality_messages(self, quality):
        templates = self._get_quality_message_templates().get(quality.lower())
        if templates is None:
            return None, None

        ra_params_template, register_set_template = templates
        ra_params = Message(copy.deepcopy(ra_params_template))
        register_set = Message(copy.deepcopy(register_set_template))
        return ra_params, register_set

    def get_ra_params_for_register_set(self, set_values):
        for ra_params_template, register_set_template in self._get_quality_message_templates().values():
            quality_set_values = register_set_template.get('SetValues', {})
            if all(set_values.get(key) == value for key, value in quality_set_values.items()):
                return Message(copy.deepcopy(ra_params_template))

        return None

    def build_default_register_set(self, wifi_country_code=None, video_anti_flicker_rate=None, video_quality_default=None):
        bootstrap_defaults = self.get_bootstrap_defaults()
        wifi_country_code = wifi_country_code or bootstrap_defaults['WifiCountryCode']
        video_anti_flicker_rate = video_anti_flicker_rate if video_anti_flicker_rate is not None else bootstrap_defaults['VideoAntiFlickerRate']
        video_quality_default = video_quality_default or bootstrap_defaults['VideoQualityDefault']

        if self.model_number.startswith('VMC5040'):
            registerSet = Message(copy.deepcopy(arlo.messages.REGISTER_SET_INITIAL_ULTRA))
        elif self._is_floodlight():
            registerSet = Message(copy.deepcopy(arlo.messages.REGISTER_SET_INITIAL_FLOODLIGHT))
        else:
            registerSet = Message(copy.deepcopy(arlo.messages.REGISTER_SET_INITIAL_SUBSCRIPTION))

        registerSet['SetValues']['WifiCountryCode'] = wifi_country_code
        registerSet['SetValues']['VideoAntiFlickerRate'] = video_anti_flicker_rate

        if video_quality_default == 'default':
            video_quality_default = 'insane'

        _, quality_register_set = self._get_quality_messages(video_quality_default)
        if quality_register_set is not None:
            registerSet['SetValues'].update(copy.deepcopy(quality_register_set['SetValues']))

        return registerSet

    def send_initial_register_set(self, wifi_country_code, video_anti_flicker_rate=None, video_quality_default='default'):
        if not self.model_number.startswith('VMC5040') and not self._is_floodlight():
            # Preserve existing startup behavior without persisting this bootstrap-only command.
            self.arm({"PIRTargetState": "Armed"}, persist_default=False)

        if self.default_register_set is None:
            self.set_default_register_set(
                self.build_default_register_set(
                    wifi_country_code,
                    video_anti_flicker_rate,
                    video_quality_default
                )
            )

        return self.send_default_register_set()

    def pir_led(self, args):
        enabled = args['enabled']
        sensitivity = args['sensitivity']

        set_values = {
            "PIREnableLED": enabled,
            "PIRLEDSensitivity": sensitivity
        }

        return self.send_register_set_values(set_values)

    def set_activity_zones(self, args):
        activity_zones = Message(copy.deepcopy(arlo.messages.ACTIVITY_ZONE_ALL))
        # TODO:Set The Co-ordinates
        return self.send_message(activity_zones)

    def unset_activity_zones(self, args):
        activity_zones = Message(copy.deepcopy(arlo.messages.ACTIVITY_ZONE_DELETE))
        return self.send_message(activity_zones)

    def set_quality(self, args):
        ra_params, registerSet = self._get_quality_messages(args["quality"])
        if ra_params is None or registerSet is None:
            return False

        result = self.send_message(ra_params) and self.send_message(registerSet)
        if result:
            self.update_default_register_set(registerSet['SetValues'])

        return result

    def arm(self, args, persist_default=True):
        pir_target_state = args['PIRTargetState']
        pir_start_sensitivity = args.get('PIRStartSensitivity') or 80
        pir_action = args.get('PIRAction') or 'Stream'
        video_motion_estimation_enable = args.get('VideoMotionEstimationEnable') or False
        audio_target_state = args.get('AudioTargetState') or 'Disarmed'

        set_values = {
            "PIRTargetState": pir_target_state,
            "PIRStartSensitivity": pir_start_sensitivity,
            "PIRAction": pir_action,
            "VideoMotionEstimationEnable": video_motion_estimation_enable,
            "VideoMotionSensitivity": 80,
            "AudioTargetState": audio_target_state,
            # Unclear what this does, only set in normal traffic when 'Disarmed'
            "DefaultMotionStreamTimeLimit": 10
        }

        return self.send_register_set_values(set_values, persist_default=persist_default)

    def set_user_stream_active(self, active):
        set_values = {
            'UserStreamActive': int(active)
        }
        # never persisted: a default of UserStreamActive=1 would make the camera stream on every registration
        return self.send_register_set_values(set_values, persist_default=False)

    # ---- arlo-local additions ----
    SPOTLIGHT_MODES = {"constant": 0, "flash": 1, "pulsate": 2}

    def get_settings(self, names=None):
        """registerGet of the known setting registers (arlo.messages.SETTINGS_KEYS) -> {name: value} or None."""
        return self.register_get(list(names) if names else arlo.messages.SETTINGS_KEYS)

    def set_settings(self, values):
        """registerSet of a whitelisted subset; persisted as the device default (re-sent on every registration).
        Returns (ok, unknown_keys). UserStreamActive and SpotlightEnabled are momentary and never persisted."""
        unknown = [k for k in values if k not in arlo.messages.SETTINGS_KEYS]
        if unknown:
            return None, unknown
        momentary = {k: v for k, v in values.items() if k in ("UserStreamActive", "SpotlightEnabled")}
        durable = {k: v for k, v in values.items() if k not in momentary}
        ok = True
        if durable:
            ok = self.send_register_set_values(durable)
        if ok and momentary:
            ok = self.send_register_set_values(momentary, persist_default=False)
        return ok, []

    def spotlight(self, args):
        """{"on": bool, "intensity": 0-100 (%), "mode": constant|flash|pulsate, "duration": seconds}
        Intensity / mode / duration are persisted settings; SpotlightEnabled is momentary (the camera turns the
        light off after SpotlightDurationManual)."""
        settings = {}
        if "intensity" in args:
            pct = max(0, min(100, float(args["intensity"])))
            settings["SpotlightIntensityManual"] = int(round(pct * 257))      # 25700 == 100 %
        if "mode" in args:
            settings["SpotlightModeManual"] = self.SPOTLIGHT_MODES.get(str(args["mode"]).lower(), 0)
        if "duration" in args:
            settings["SpotlightDurationManual"] = int(args["duration"])
        if settings and not self.send_register_set_values(settings):
            return False
        return self.send_register_set_values({"SpotlightEnabled": bool(args.get("on", True))}, persist_default=False)

    def siren(self, args):
        """{"state": "on"|"off", "duration": s, "volume": 1-8, "pattern": "alarm"} -> the camera's response is in
        self.last_ack (the REST returns it: the accepted SirenState strings are still being established)."""
        msg = Message(copy.deepcopy(arlo.messages.SIREN))
        state = args.get("state", "on")
        msg["SirenState"] = state if isinstance(state, str) else ("on" if state else "off")
        for key, field in (("duration", "Duration"), ("volume", "Volume"), ("pattern", "Pattern")):
            if key in args:
                msg[field] = args[key]
        self.last_ack = None
        return self.send_message(msg)

    def snapshot_request(self, url):
        _snapshot_request = Message(copy.deepcopy(arlo.messages.SNAPSHOT))
        _snapshot_request['DestinationURL'] = url
        return self.send_message(_snapshot_request)
