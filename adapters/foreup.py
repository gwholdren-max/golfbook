"""
ForeUp Software adapter.

Unlike WebTrac, ForeUp's booking page is a thin client over a public JSON
endpoint, so this reads availability over plain HTTP — no browser, no
Playwright, a few hundred milliseconds per day instead of ~6 seconds.

Finding the config values for a new ForeUp course: open the course's
booking page, pick a rate type, and watch the request to
/api/booking/times. Its query string carries courseId, scheduleId and
bookingClass.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Some courses sit on foreupsoftware.com, others on app.foreupsoftware.com;
# a course's config can override `host`.
DEFAULT_HOST = 'foreupsoftware.com'
API = 'https://{host}/index.php/api/booking/times'
BOOKING_PAGE = 'https://{host}/index.php/booking/{course}/{schedule}#/teetimes'

# ForeUp 403s a bare urllib user-agent
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/126.0 Safari/537.36'),
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
}


class ForeUpAdapter:
    name = 'foreup'

    def _fetch_day(self, course, date):
        """One day's tee times. `date` is a datetime."""
        params = {
            'time': 'all',
            'date': date.strftime('%m-%d-%Y'),
            'holes': 'all',
            'players': '0',
            'booking_class': str(course['bookingClass']),
            'schedule_id': str(course['scheduleId']),
            'schedule_ids[]': str(course['scheduleId']),
            'specials_only': '0',
            'api_key': '',
        }
        host = course.get('host') or DEFAULT_HOST
        url = API.format(host=host) + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read().decode('utf-8')

        data = json.loads(body)
        if not isinstance(data, list):
            # ForeUp answers an empty day with {} rather than []
            return []
        return data

    async def fetch(self, course):
        days_ahead = int(course.get('daysAhead', 8))
        book_url = BOOKING_PAGE.format(host=course.get('host') or DEFAULT_HOST,
                                       course=course['courseId'],
                                       schedule=course['scheduleId'])
        today = datetime.now()
        days = []

        for offset in range(days_ahead):
            date = today + timedelta(days=offset)
            label = date.strftime('%m/%d/%Y')
            try:
                raw = self._fetch_day(course, date)
            except urllib.error.HTTPError as e:
                if e.code in (404, 422):      # day outside the booking window
                    days.append({'date': label, 'slots': []})
                    continue
                raise
            except (urllib.error.URLError, ValueError) as e:
                logger.warning("%s %s: %s", course['name'], label, e)
                days.append({'date': label, 'slots': []})
                continue

            slots = []
            for t in raw:
                stamp = t.get('time', '')          # "2026-10-27 08:20"
                clock = stamp.split(' ')[-1] if ' ' in stamp else stamp
                try:
                    hh, mm = clock.split(':')[:2]
                    minutes = int(hh) * 60 + int(mm)
                    pretty = datetime(2000, 1, 1, int(hh), int(mm)) \
                        .strftime('%-I:%M %p').lower()
                except ValueError:
                    minutes, pretty = 0, clock

                holes = t.get('holes')
                side = t.get('teesheet_side_name') or ''
                slots.append({
                    'time': pretty,
                    'spots': int(t.get('available_spots') or 0),
                    'holes': ('%s (%s)' % (holes, side)) if holes and side
                             else (str(holes) if holes else ''),
                    'bookUrl': book_url,
                    '_min': minutes,
                })

            # Sort on minutes, not the formatted string — "8:52 am" sorts
            # after "2:52 pm" lexically.
            slots.sort(key=lambda s: s.pop('_min'))
            days.append({'date': label, 'slots': slots})

        open_days = [d['date'] for d in days if d['slots']]
        return {
            'days': days,
            'window': {
                'min': open_days[0] if open_days else days[0]['date'],
                'max': open_days[-1] if open_days else days[-1]['date'],
            },
        }
