import flask
import threading
import sqlite3
import functools
import os
from flask import send_file
import io
from arlo.device_db import DeviceDB
from arlo.device import Device
from arlo.camera import Camera

app = flask.Flask(__name__)
app.config["DEBUG"] = False
app.use_reloader = False


def validate_device_request(body_required=True):
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            device = DeviceDB.from_db_serial(kwargs['serial'])
            if device is None:
                flask.abort(404)
            kwargs['device'] = device

            if body_required:
                req_body = flask.request.get_json()
                if req_body is None:
                    flask.abort(400)
                kwargs['req_body'] = req_body

            return f(*args, **kwargs)
        return wrapper
    return decorator


@app.route('/', methods=['GET'])
def home():
    return "PING"


@app.route('/device', methods=['GET'])
def list():
    DeviceDB.ensure_schema()
    with sqlite3.connect('arlo.db') as conn:
        c = conn.cursor()
        c.execute("SELECT ip, hostname, serialnumber, friendlyname FROM devices")
        rows = c.fetchall()
        devices = []
        if rows is not None:
            for row in rows:
                (ip, hostname, serial_number, friendly_name) = row
                devices.append({"ip": ip, "hostname": hostname,
                               "serial_number": serial_number, "friendly_name": friendly_name})

        return flask.jsonify(devices)


@app.route('/device/<serial>', methods=['GET', 'DELETE'])
@validate_device_request(body_required=False)
def device(serial, device: Device):
    if flask.request.method == 'DELETE':
        return flask.jsonify({"result": DeviceDB.delete(device)})
    elif device.status is None:
        return flask.jsonify({})
    else:
        return flask.jsonify(device.status.dictionary)


@app.route('/device/<serial>/registration', methods=['GET'])
@validate_device_request(body_required=False)
def registration(serial, device: Device):
    if device.registration is None:
        return flask.jsonify({})
    else:
        return flask.jsonify(device.registration.dictionary)


@app.route('/device/<serial>/statusrequest', methods=['POST'])
@validate_device_request(body_required=False)
def status_request(serial, device: Device):
    result = device.status_request()
    return flask.jsonify({"result": result})


@app.route('/device/<serial>/userstreamactive', methods=['POST'])
@validate_device_request()
def user_stream_active(serial, req_body, device: Camera):
    # arlo-local: this used to be a no-op that answered {"result": true} without talking to the camera.
    active = req_body.get("active")
    if active is None:
        flask.abort(400)
    result = device.set_user_stream_active(int(active))
    return flask.jsonify({"result": result})


@app.route('/device/<serial>/arm', methods=['POST'])
@validate_device_request()
def arm(serial, req_body, device: Device):
    result = device.arm(req_body)
    return flask.jsonify({"result": result})


@app.route('/device/<serial>/pirled', methods=['POST'])
@validate_device_request()
def pir_led(serial, req_body, device: Camera):
    result = device.pir_led(req_body)
    return flask.jsonify({"result": result})


@app.route('/device/<serial>/quality', methods=['POST'])
@validate_device_request()
def set_quality(serial, req_body, device: Camera):
    if req_body['quality'] is None:
        flask.abort(400)
    else:
        result = device.set_quality(req_body)
        return flask.jsonify({"result": result})


@app.route('/device/<serial>/snapshot', methods=['POST'])
@validate_device_request()
def request_snapshot(serial, req_body, device: Camera):
    if req_body['url'] is None:
        flask.abort(400)
    else:
        result = device.snapshot_request(req_body['url'])
        return flask.jsonify({"result": result})


@app.route('/device/<serial>/audiomic', methods=['POST'])
@validate_device_request()
def request_mic(serial, req_body, device: Camera):
    if req_body['enabled'] is None:
        flask.abort(400)
    else:
        result = device.mic_request(req_body['enabled'])
        return flask.jsonify({"result": result})


@app.route('/device/<serial>/audiospeaker', methods=['POST'])
@validate_device_request()
def request_speaker(serial, req_body, device: Device):
    if req_body['enabled'] is None:
        flask.abort(400)
    else:
        result = device.speaker_request(req_body['enabled'])
        return flask.jsonify({"result": result})


@app.route('/device/<serial>/friendlyname', methods=['POST'])
@validate_device_request()
def set_friendlyname(serial, req_body, device: Device):
    if req_body['name'] is None:
        flask.abort(400)
    else:
        device.friendly_name = req_body['name']
        DeviceDB.persist(device)
        return flask.jsonify({"result": True})


@app.route('/device/<serial>/activityzones', methods=['POST', 'DELETE'])
@validate_device_request()
def set_activity_zones(serial, req_body, device: Camera):
    if flask.request.method == 'DELETE':
        result = device.unset_activity_zones()
    else:
        result = device.set_activity_zones(req_body)

    return flask.jsonify({"result": result})


@app.route('/snapshot/<identifier>/', methods=['POST'])
def receive_snapshot(identifier):
    # Cameras upload the fullSnapshot as multipart/form-data with a part named "file" whose
    # filename is EMPTY (seen on Pro 4 VMC4041PB and Pro 5S VMC4060B), so an empty filename must
    # not be rejected. Accept any multipart file part regardless of its name, and a raw JPEG body
    # (image/jpeg or application/octet-stream) for firmwares that do not use multipart.
    start_path = os.path.abspath('/tmp')
    target_path = os.path.join(start_path, f"{identifier}.jpg")
    common_prefix = os.path.commonprefix([target_path, start_path])
    if common_prefix != start_path:
        flask.abort(400)

    content_type = flask.request.content_type or ''
    if flask.request.files:
        file = flask.request.files.get('file') or next(iter(flask.request.files.values()))
        data = file.read()
    elif content_type.startswith(('image/', 'application/octet-stream')) or not content_type.startswith('multipart/'):
        data = flask.request.get_data()
    else:
        flask.abort(400)
    if not data:
        flask.abort(400)

    tmp_path = target_path + '.part'
    with open(tmp_path, 'wb') as fo:
        fo.write(data)
    os.replace(tmp_path, target_path)
    app.logger.info(f"snapshot from {identifier}: {len(data)} bytes ({content_type.split(';')[0]})")
    return flask.jsonify({"result": True, "bytes": len(data)})


@app.route('/snapshot/<identifier>', methods=['GET'])
def get_snapshot(identifier):
    start_path = os.path.abspath('/tmp')
    target_path = os.path.join(start_path, f"{identifier}.jpg")
    common_prefix = os.path.commonprefix([target_path, start_path])
    if (common_prefix != start_path or not os.path.isfile(target_path)):
        flask.abort(400)
    else:
        # read the file into memory
        return_data = io.BytesIO()
        with open(target_path, 'rb') as fo:
            return_data.write(fo.read())
        # after writing, cursor will be at last byte, so move it to start
        return_data.seek(0)
        # delete the file
        if flask.request.args.get('delete') == '1':
            os.remove(target_path)   # one-shot read only on request; keep the file for thumbnails
        # send it to client
        return send_file(return_data, mimetype='image/jpeg', download_name=f'{identifier}.jpg')


# ---- arlo-local additions: settings / spotlight / siren ----

@app.route('/device/<serial>/settings', methods=['GET', 'POST'])
@validate_device_request(body_required=False)
def settings(serial, device: Camera):
    """
    GET  -> {"result": true, "values": {register: value, ...}}   (registerGet of arlo.messages.SETTINGS_KEYS)
    POST {"NightVisionMode": false, "PIRStartSensitivity": 60, ...}
         -> registerSet of those keys (whitelisted, persisted as the device default), then the values read back:
            {"result": true, "values": {...}}; unknown keys -> 400 {"unknown": [...]}
    """
    if flask.request.method == 'GET':
        values = device.get_settings()
        return flask.jsonify({"result": values is not None, "values": values or {}})
    body = flask.request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        flask.abort(400)
    ok, unknown = device.set_settings(body)
    if unknown:
        return flask.jsonify({"result": False, "unknown": unknown}), 400
    values = device.get_settings(body.keys()) if ok else None
    return flask.jsonify({"result": bool(ok), "values": values or {}})


@app.route('/device/<serial>/spotlight', methods=['POST'])
@validate_device_request()
def spotlight(serial, req_body, device: Camera):
    """{"on": true|false, "intensity": 0-100, "mode": "constant"|"flash"|"pulsate", "duration": seconds}"""
    result = device.spotlight(req_body)
    return flask.jsonify({"result": result})


@app.route('/device/<serial>/reboot', methods=['POST'])
@validate_device_request(body_required=False)
def reboot(serial, device: Camera):
    """registerSet {"Reboot": 1}: the camera acknowledges, drops off the network at once and re-registers ~1-2 min
    later (Pro 4 VMC4041PB, 2026-09-22). The way out of a stuck user stream (a session the camera keeps feeding
    after its client died: UserStreamActive reads 1, UserStreamed keeps counting, no new client is accepted)."""
    result = device.send_register_set_values({"Reboot": 1}, persist_default=False)
    return flask.jsonify({"result": result})


@app.route('/device/<serial>/factoryreset', methods=['POST'])
@validate_device_request(body_required=False)
def factory_reset(serial, device: Camera):
    """registerSet {"FactoryReset": 1}: the camera forgets its Wi-Fi and base station and waits for a new sync —
    what the Arlo app does on "remove device". Never persisted (a persisted Reboot put a camera in a loop)."""
    result = device.send_register_set_values({"FactoryReset": 1}, persist_default=False)
    return flask.jsonify({"result": result})


@app.route('/device/<serial>/siren', methods=['POST'])
@validate_device_request()
def siren(serial, req_body, device: Camera):
    """{"state": "on"|"off", "duration": s, "volume": 1-8, "pattern": "alarm"} -> the camera's raw response too"""
    result = device.siren(req_body)
    response = device.last_ack.dictionary if device.last_ack is not None else None
    return flask.jsonify({"result": result, "response": response})


@app.route('/device/<serial>/message', methods=['POST'])
@validate_device_request()
def message(serial, req_body, device: Device):
    result = device.send_message_dict(req_body)
    return flask.jsonify({"result": result})


@app.route('/device/<serial>/registerset', methods=['POST'])
@validate_device_request()
def register_set(serial, req_body, device: Device):
    result = device.register_set(req_body)
    return flask.jsonify({"result": result})


@app.route('/device/<serial>/registerget', methods=['POST'])
@validate_device_request()
def register_get(serial, req_body, device: Device):
    """
    Read register values back off a device. See Device.register_get.

    POST /device/<serial>/registerget
    { "names": ["NightVisionMode", "DuskToDawnThrshVal"] }
    """
    names = req_body.get('names')
    # NB: this module defines a route function called list(), so the builtin is
    # shadowed here and isinstance(names, list) would raise TypeError.
    if not names or isinstance(names, (str, dict)):
        flask.abort(400)

    values = device.register_get(names)
    return flask.jsonify({"result": values is not None, "values": values or {}})


def get_thread():
    return threading.Thread(target=app.run(host='0.0.0.0'))
