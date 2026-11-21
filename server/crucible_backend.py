"""HTTP request dispatcher with an application-supplied route table."""

import json
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse


ROUTERS = []
_REQUEST = threading.local()


class Handler(BaseHTTPRequestHandler):
    def _route(self, body):
        parsed = urlparse(self.path)
        _REQUEST.query = parsed.query
        parts = parsed.path.strip('/').split('/', 1)
        if len(parts) != 2:
            return None
        service, rest = parts
        for name, handler in ROUTERS:
            if name == service:
                return handler('/' + rest, body)
        return None

    def _serve(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b''
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        try:
            result = self._route(body)
        except Exception as exc:
            result = {'error': str(exc)}
        if result is None:
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            payload = {'error': 'unknown route', 'path': self.path}
            self.wfile.write(json.dumps(payload).encode())
            return
        data = json.dumps(result).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _serve
    do_POST = _serve
    do_PUT = _serve
