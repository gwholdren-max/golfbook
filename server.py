"""
Tee time website — serves web/index.html and the data the collector writes.

    python3 server.py                  # http://localhost:8080
    python3 server.py --port 9000
    python3 server.py --host 0.0.0.0   # reachable from the local network

Stdlib only, so there's nothing to install on the box that runs it.
Routes:
    GET /                 the site
    GET /api/tee-times    collector output as JSON
    GET /healthz          "ok" plus the age of the data, for a watchdog
"""

import argparse
import gzip
import hashlib
import json
import os
import posixpath
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, 'web')
DATA_FILE = os.path.join(BASE_DIR, 'data', 'tee_times.json')

# Below this, compressing costs more than it saves
GZIP_MIN_BYTES = 1024
GZIP_TYPES = ('text/', 'application/javascript', 'application/json', 'image/svg')

CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
}


def read_data():
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except IOError:
        return {'generatedAt': None, 'courses': [], 'error': 'no_data'}
    except ValueError:
        return {'generatedAt': None, 'courses': [], 'error': 'bad_data'}


def data_age_seconds(payload):
    stamp = payload.get('generatedAt')
    if not stamp:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(stamp)).total_seconds()
    except ValueError:
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = 'TeeTimes/1.0'

    def log_message(self, fmt, *args):
        # One tidy line per request instead of the default noise
        print('%s - %s' % (self.address_string(), fmt % args))

    def _send(self, code, body, content_type, cache='no-store'):
        """
        Send a response, compressed and revalidatable.

        Both matter on a metered tunnel: the tee time JSON gzips by ~97%
        (it is the same few keys repeated a thousand times), and the page
        polls every 60s while the data only changes every 5 min, so most
        polls can be answered with a bodyless 304.
        """
        if isinstance(body, str):
            body = body.encode('utf-8')

        etag = '"%s"' % hashlib.md5(body).hexdigest()[:16]

        if code == 200 and self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', cache)
            self.end_headers()
            return

        encoding = None
        if (code == 200
                and len(body) >= GZIP_MIN_BYTES
                and content_type.startswith(GZIP_TYPES)
                and 'gzip' in self.headers.get('Accept-Encoding', '')):
            body = gzip.compress(body, 6)
            encoding = 'gzip'

        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.send_header('ETag', etag)
        if encoding:
            self.send_header('Content-Encoding', encoding)
            self.send_header('Vary', 'Accept-Encoding')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj), CONTENT_TYPES['.json'])

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split('?', 1)[0]

        if path == '/api/tee-times':
            return self._send_json(200, read_data())

        if path == '/healthz':
            payload = read_data()
            age = data_age_seconds(payload)
            healthy = age is not None and age < 1800
            return self._send_json(200 if healthy else 503, {
                'ok': healthy,
                'generatedAt': payload.get('generatedAt'),
                'ageSeconds': None if age is None else round(age),
            })

        if path == '/':
            path = '/index.html'

        # Resolve inside web/ only — no climbing out with ../
        rel = posixpath.normpath(path).lstrip('/')
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(WEB_DIR + os.sep) or not os.path.isfile(full):
            return self._send(404, 'Not found', 'text/plain; charset=utf-8')

        ext = os.path.splitext(full)[1].lower()
        with open(full, 'rb') as f:
            body = f.read()
        self._send(200, body, CONTENT_TYPES.get(ext, 'application/octet-stream'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1',
                        help='bind address (0.0.0.0 to expose on the LAN)')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    shown = 'localhost' if args.host == '127.0.0.1' else args.host
    print('Tee times on http://%s:%d  (Ctrl-C to stop)' % (shown, args.port))
    if not os.path.isfile(DATA_FILE):
        print('No data yet — run: python3 collector.py --once')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
        server.server_close()


if __name__ == '__main__':
    main()
