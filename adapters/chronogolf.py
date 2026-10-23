"""
Chronogolf (Lightspeed Golf) adapter.

Chronogolf's marketplace exposes a public JSON endpoint:

    https://www.chronogolf.com/marketplace/v2/teetimes
        ?start_date=YYYY-MM-DD&course_ids=<uuid>[,<uuid>...]
        &holes=9,18&start_time=HH:MM&page=N

No auth, no browser. Results are paged ~24 at a time and the response
carries no page count, so this walks pages until one comes back empty.

A club can list several course UUIDs (Wescott is 27 holes sold as three
nines). Passing them together returns the union, so one query per day
covers the club. Times are deduplicated on the tee time's own uuid
because consecutive pages can overlap.

The same clock time can appear once per nine. Those are merged into one
slot — a golfer wants to know 8:08 is open, not which nine it is — taking
the largest bookable group and the cheapest rate across them.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

API = 'https://www.chronogolf.com/marketplace/v2/teetimes'
CLUB = 'https://www.chronogolf.com/club/{slug}?date={date}'

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/126.0 Safari/537.36'),
    'Accept': 'application/json',
}

MAX_PAGES = 12          # guard against a server that never returns empty
THROTTLE = 1.2          # seconds between requests — Chronogolf 429s readily
RETRY_WAITS = (2, 5, 12)


class ChronogolfAdapter:
    name = 'chronogolf'

    def _page(self, course_ids, date, page):
        params = {
            'start_date': date.strftime('%Y-%m-%d'),
            'course_ids': ','.join(course_ids),
            'holes': '9,18',
            'start_time': '06:00',
            'page': str(page),
        }
        url = API + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=HEADERS)

        # Chronogolf rate-limits a burst of page requests. Back off and
        # retry rather than letting a 429 look like an empty day.
        for attempt, wait in enumerate((0,) + RETRY_WAITS):
            if wait:
                logger.info("Chronogolf 429 — retrying in %ss", wait)
                time.sleep(wait)
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    body = json.loads(resp.read().decode('utf-8'))
                time.sleep(THROTTLE)
                return body.get('teetimes') or []
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == len(RETRY_WAITS):
                    raise
        return []

    def _fetch_day(self, course_ids, date):
        found = {}
        for page in range(1, MAX_PAGES + 1):
            batch = self._page(course_ids, date, page)
            if not batch:
                break
            before = len(found)
            for t in batch:
                key = t.get('uuid') or t.get('id')
                if key is not None:
                    found[key] = t
            if len(found) == before:
                break          # a page of pure duplicates means we're done
        return list(found.values())

    @staticmethod
    def _slot(t, book_url):
        clock = t.get('start_time') or ''          # "8:00", 24-hour
        try:
            hh, mm = clock.split(':')[:2]
            hour, minute = int(hh), int(mm)
            minutes = hour * 60 + minute
            pretty = '%d:%02d %s' % (hour % 12 or 12, minute,
                                     'am' if hour < 12 else 'pm')
        except ValueError:
            minutes, pretty = 0, clock

        price = None
        dp = t.get('default_price') or {}
        for key in ('subtotal', 'green_fee'):
            if isinstance(dp.get(key), (int, float)):
                price = float(dp[key])
                break

        bookable = (t.get('course') or {}).get('bookable_holes') or []
        holes = '/'.join(str(h) for h in bookable) if bookable else ''

        return {
            'time': pretty,
            'spots': int(t.get('max_player_size') or 0),
            'holes': holes,
            'price': price,
            'bookUrl': book_url,
            '_min': minutes,
        }

    async def fetch(self, course):
        course_ids = course['courseIds']
        if isinstance(course_ids, str):
            course_ids = [course_ids]
        slug = course.get('slug', '')
        days_ahead = int(course.get('daysAhead', 8))
        today = datetime.now()
        days = []

        for offset in range(days_ahead):
            date = today + timedelta(days=offset)
            label = date.strftime('%m/%d/%Y')
            book_url = CLUB.format(slug=slug, date=date.strftime('%Y-%m-%d'))
            try:
                raw = self._fetch_day(course_ids, date)
            except (urllib.error.URLError, ValueError) as e:
                # Don't render a failed read as "nothing available" — let the
                # collector keep the previous good data for this course.
                raise RuntimeError("%s: %s" % (label, e))

            merged = {}
            for t in raw:
                slot = self._slot(t, book_url)
                if slot['spots'] <= 0:
                    continue
                prior = merged.get(slot['time'])
                if prior is None:
                    merged[slot['time']] = slot
                    continue
                # Same time on another nine: keep the best of the two
                prior['spots'] = max(prior['spots'], slot['spots'])
                if slot['price'] is not None and (
                        prior['price'] is None or slot['price'] < prior['price']):
                    prior['price'] = slot['price']

            slots = sorted(merged.values(), key=lambda s: s['_min'])
            for s_ in slots:
                s_.pop('_min', None)
            days.append({'date': label, 'slots': slots})

        open_days = [d['date'] for d in days if d['slots']]
        return {
            'days': days,
            'window': {
                'min': open_days[0] if open_days else days[0]['date'],
                'max': open_days[-1] if open_days else days[-1]['date'],
            },
        }
