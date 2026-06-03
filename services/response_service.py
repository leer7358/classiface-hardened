from flask import jsonify

def ok(data=None, message="ok", code=200):
    payload = {"ok": True, "message": message}
    if data is not None:
        payload["data"] = data
    return jsonify(payload), code

def fail(message="error", code=400, data=None):
    payload = {"ok": False, "message": message}
    if data is not None:
        payload["data"] = data
    return jsonify(payload), code
