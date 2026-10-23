"""
GolfNow / EZLinks adapter.

The course-branded EZLinks sites (patriotspoint.ezlinksgolf.com and the
like) sit behind Cloudflare bot protection and can't be read without a
real browser. GolfNow's own marketplace carries the same inventory and
answers a plain JSON POST, so this reads the marketplace instead:

    POST https://www.golfnow.com/api/tee-times/tee-time-search-results

No auth, no browser, no Cloudflare challenge. The body is a large search
form; the fields that matter are `facilityId`, `date` ("Oct 27 2026") and
the paging/rate filters.

Finding a facility id: search golfnow.com for the course — its tee time
page is /tee-times/facility/<id>-<slug>/search.

Caveat worth knowing: this is GolfNow's view of the tee sheet, which is
the inventory the course has given GolfNow, not necessarily every time
the pro shop could sell you. Expect it to under-report relative to a
course's own booking page.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

API = 'https://www.golfnow.com/api/tee-times/tee-time-search-results'
SUMMARIES = ('https://www.golfnow.com/api/tee-times/tee-times/'
             'facility/{facility}/summaries/from/{start}/to/{end}')
FACILITY_PAGE = 'https://www.golfnow.com/tee-times/facility/{facility}/search'
SITE = 'https://www.golfnow.com'

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/126.0 Safari/537.36'),
    'Accept': 'application/json',
    'Content-Type': 'application/json',
}

THROTTLE = 0.4

# GolfNow spells the bookable group sizes as words: "Two", "TwoThreeFour".
_WORDS = (('Four', 4), ('Three', 3), ('Two', 2), ('One', 1))


def _max_players(rule):
    """Largest group the rule allows — 'TwoThreeFour' -> 4."""
    for word, n in _WORDS:
        if word in (rule or ''):
            return n
    return 0


class GolfNowAdapter:
    name = 'golfnow'

    def _search(self, facility, date, location):
        body = {
            'pageSize': 100, 'teeTimeCount': 100, 'pageNumber': 0,
            'date': date.strftime('%b %d %Y'),
            'sortBy': 'Date', 'sortByRollup': 'Date.MinDate', 'sortDirection': 0,
            'hotDealsOnly': False, 'golfPassPerksOnly': False,
            'bestDealsOnly': False, 'promotedCampaignsOnly': False,
            'priceMin': 0, 'priceMax': 10000, 'players': 0,
            'timePeriod': 'Any', 'timeMin': 0, 'timeMax': 48,
            'holes': 'Any', 'facilityType': 'GolfCourse',
            'radius': 35, 'facilityId': int(facility), 'facilityIds': [],
            'searchType': 'Facility', 'view': 'Grouping',
            'excludeFeaturedFacilities': False, 'excludePrivateFacilities': False,
            'rateType': 'all', 'trackmanOnly': False, 'teeTimeId': 0,
            'sortDisplay': '', 'location': location,
            'offPlatformCourse': False, 'disableLocationChange': False,
            'redirectOnSearch': False,
        }
        headers = dict(HEADERS)
        headers['Referer'] = FACILITY_PAGE.format(facility=facility)
        req = urllib.request.Request(
            API, data=json.dumps(body).encode('utf-8'),
            headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        time.sleep(THROTTLE)
        return ((payload.get('ttResults') or {}).get('teeTimes')) or []

    @staticmethod
    def _slot(t):
        tm = t.get('time') or {}
        clock = tm.get('formatted') or ''
        meridiem = (tm.get('formattedTimeMeridian') or '').lower()
        try:
            hh, mm = clock.split(':')[:2]
            hour = int(hh) % 12
            if meridiem == 'pm':
                hour += 12
            minutes = hour * 60 + int(mm)
        except ValueError:
            minutes = 0

        rates = t.get('teeTimeRates') or []
        spots = max([_max_players(r.get('playerRule')) for r in rates] or [0])
        spots = max(spots, _max_players(t.get('playerRule')))

        price = None
        rate = t.get('displayRate') or t.get('minTeeTimeRate') or {}
        if isinstance(rate.get('value'), (int, float)):
            price = float(rate['value'])

        holes = sorted({r.get('holeCount') for r in rates if r.get('holeCount')})
        detail = t.get('detailUrl') or ''

        return {
            'time': '%s %s' % (clock, meridiem),
            'spots': spots,
            'holes': '/'.join(str(h) for h in holes),
            'price': price,
            'bookUrl': (SITE + detail) if detail.startswith('/') else detail,
            '_min': minutes,
        }

    async def fetch(self, course):
        facility = course['facilityId']
        location = course.get('name', '')
        days_ahead = int(course.get('daysAhead', 8))
        today = datetime.now()
        days = []

        for offset in range(days_ahead):
            date = today + timedelta(days=offset)
            label = date.strftime('%m/%d/%Y')
            try:
                raw = self._search(facility, date, location)
            except (urllib.error.URLError, ValueError) as e:
                # A failed read is not an empty day — let the collector keep
                # the last good data for this course.
                raise RuntimeError('%s: %s' % (label, e))

            slots = [self._slot(t) for t in raw]
            slots = [s for s in slots if s['spots'] > 0]
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
