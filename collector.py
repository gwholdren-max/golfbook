"""
Tee time collector — scrapes every connected course on a loop and writes
data/tee_times.json, which server.py serves.

Run it alongside the server on the dedicated machine:

    python3 collector.py                 # loop forever, default 5 minutes
    python3 collector.py --once          # single pass, useful for testing
    python3 collector.py --interval 600  # ten minutes

A course that errors keeps its previous data rather than blanking out, so
one broken adapter can't empty the site.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime

import adapters

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COURSES_FILE = os.path.join(BASE_DIR, 'courses.json')
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATA_FILE = os.path.join(DATA_DIR, 'tee_times.json')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('collector')


def load_courses():
    with open(COURSES_FILE) as f:
        return json.load(f)['courses']


def load_existing():
    """Previous run's output, so a failed course keeps its last-known times."""
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except (IOError, ValueError):
        return {'courses': []}


def write_atomic(payload):
    """Write via a temp file + rename so the server never reads a half file."""
    if not os.path.isdir(DATA_DIR):
        os.makedirs(DATA_DIR)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


async def collect_once():
    courses = load_courses()
    previous = {c['id']: c for c in load_existing().get('courses', [])}
    out = []

    for course in courses:
        platform = course.get('platform')
        entry = {
            'id': course['id'],
            'name': course['name'],
            'area': course.get('area', ''),
            'siteUrl': course.get('siteUrl', ''),
            'platform': platform,
            'coverage': course.get('coverage', 'full'),
            'coverageNote': course.get('coverageNote', ''),
            'directUrl': course.get('directUrl', ''),
        }

        if not platform:
            entry.update({'status': 'not_connected', 'days': [],
                          'updatedAt': None, 'error': None})
            out.append(entry)
            continue

        if course.get('enabled') is False:
            # Paused by config — keep the last read so the site still shows it
            stale = previous.get(course['id'], {})
            logger.info("Skipping %s (paused)", course['name'])
            entry.update({
                'status': 'paused',
                'days': stale.get('days', []),
                'window': stale.get('window'),
                'updatedAt': stale.get('updatedAt'),
                'error': None,
            })
            out.append(entry)
            continue

        adapter = adapters.get_adapter(platform)
        if not adapter:
            logger.warning("%s: no adapter for platform '%s'",
                           course['name'], platform)
            entry.update({'status': 'unsupported', 'days': [],
                          'updatedAt': None,
                          'error': "no adapter for '%s'" % platform})
            out.append(entry)
            continue

        logger.info("Fetching %s (%s)...", course['name'], platform)
        started = time.time()
        try:
            result = await adapter.fetch(course)
            entry.update({
                'status': 'ok',
                'days': result.get('days', []),
                'window': result.get('window'),
                'updatedAt': datetime.now().isoformat(timespec='seconds'),
                'error': None,
            })
            total = sum(len(d.get('slots', [])) for d in entry['days'])
            logger.info("  %s: %d slots in %.1fs",
                        course['name'], total, time.time() - started)
        except Exception as e:
            logger.error("  %s failed: %s", course['name'], e)
            stale = previous.get(course['id'], {})
            entry.update({
                'status': 'error',
                'days': stale.get('days', []),
                'window': stale.get('window'),
                'updatedAt': stale.get('updatedAt'),
                'error': str(e)[:200],
            })

        out.append(entry)

    payload = {
        'generatedAt': datetime.now().isoformat(timespec='seconds'),
        'courses': out,
    }
    write_atomic(payload)

    live = sum(1 for c in out if c['status'] == 'ok')
    slots = sum(len(d.get('slots', [])) for c in out for d in c.get('days', []))
    logger.info("Wrote %s — %d/%d courses live, %d slots total",
                os.path.relpath(DATA_FILE, BASE_DIR), live, len(out), slots)
    return payload


async def loop_forever(interval):
    while True:
        started = time.time()
        try:
            await collect_once()
        except Exception as e:
            logger.error("Collection pass failed: %s", e)
        elapsed = time.time() - started
        wait = max(10, interval - elapsed)
        logger.info("Next pass in %.0fs", wait)
        await asyncio.sleep(wait)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true',
                        help='run a single collection pass and exit')
    parser.add_argument('--interval', type=int, default=300,
                        help='seconds between passes (default: 300)')
    args = parser.parse_args()

    try:
        if args.once:
            asyncio.run(collect_once())
        else:
            logger.info("Collecting every %ds. Ctrl-C to stop.", args.interval)
            asyncio.run(loop_forever(args.interval))
    except KeyboardInterrupt:
        logger.info("Stopped.")
        sys.exit(0)


if __name__ == '__main__':
    main()
