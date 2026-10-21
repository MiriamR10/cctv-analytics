"""Minimal client for the central forensic service (stdlib only).

Publishing is optional: when FORENSIC_URL is not set, nothing is sent.
"""
import json
import os
import urllib.request

FORENSIC_URL = os.environ.get('FORENSIC_URL', '').rstrip('/')


def _request(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(FORENSIC_URL + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def ensure_camera(name, zone):
    """Return the id of the camera with this name, creating it if needed."""
    for camera in _request('GET', '/cameras'):
        if camera['name'] == name:
            return camera['id']
    return _request('POST', '/cameras', {'name': name, 'zone': zone})['id']


def publish_events(events, camera_name, zone):
    """Send events to /events/bulk. Returns the number of events stored."""
    if not FORENSIC_URL or not events:
        return 0
    camera_id = ensure_camera(camera_name, zone)
    for event in events:
        event['camera_id'] = camera_id
        event['zone'] = zone
    return len(_request('POST', '/events/bulk', events))
