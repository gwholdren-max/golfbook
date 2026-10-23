"""
Quick18 / Sagacity Golf adapter.

Quick18 serves a plain server-rendered table at

    https://<course>.quick18.com/teetimes/searchmatrix?teedate=YYYYMMDD

so this reads availability over HTTP with no browser. Each row is one tee
time; the rate columns to its right are price tiers (public, local, 9-hole
and so on). A tier with a "Select" link is bookable; one marked
`mtrxInactive` is not, so a row with no Select link anywhere is full.

Quick18 reports a *player range* ("1 to 4 players") rather than a count of
open seats, so `spots` is the top of that range — the largest group that
can still book this time.
"""

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

SEARCH = 'https://{host}.quick18.com/teetimes/searchmatrix?teedate={date}'
ORIGIN = 'https://{host}.quick18.com'

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/126.0 Safari/537.36'),
    'Accept': 'text/html,application/xhtml+xml',
}

ROW_RE     = re.compile(r'<tr\b[^>]*>(.*?)</tr>', re.S | re.I)
TIME_RE    = re.compile(
    r'<td[^>]*class="[^"]*mtrxTeeTimes[^"]*"[^>]*>\s*'
    r'(\d{1,2}:\d{2})\s*<div[^>]*>\s*([AP]M)\s*</div>', re.S | re.I)
PLAYERS_RE = re.compile(
    r'<td[^>]*class="[^"]*matrixPlayers[^"]*"[^>]*>(.*?)</td>', re.S | re.I)
SELECT_RE  = re.compile(r'<a[^>]*class="[^"]*teebutton[^"]*"[^>]*href="([^"]+)"',
                        re.S | re.I)
PRICE_RE   = re.compile(r'<div[^>]*class="[^"]*mtrxPrice[^"]*"[^>]*>\s*'
                        r'\$([\d,]+(?:\.\d{2})?)', re.S | re.I)
RANGE_RE   = re.compile(r'(\d+)\s*to\s*(\d+)', re.I)


class Quick18Adapter:
    name = 'quick18'

    def _fetch_day(self, host, date):
        url = SEARCH.format(host=host, date=date.strftime('%Y%m%d'))
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.read().decode('utf-8', 'replace')

    def _parse(self, html, host):
        origin = ORIGIN.format(host=host)
        slots = []

        for body in ROW_RE.findall(html):
            tm = TIME_RE.search(body)
            if not tm:
                continue                      # header or spacer row

            links = SELECT_RE.findall(body)
            if not links:
                continue                      # every rate tier is inactive

            clock, meridiem = tm.group(1), tm.group(2).lower()
            try:
                hh, mm = clock.split(':')
                hour = int(hh) % 12
                if meridiem == 'pm':
                    hour += 12
                minutes = hour * 60 + int(mm)
            except ValueError:
                continue

            spots = 4
            pm = PLAYERS_RE.search(body)
            if pm:
                rng = RANGE_RE.search(re.sub(r'<[^>]+>', ' ', pm.group(1)))
                if rng:
                    spots = int(rng.group(2))

            prices = [float(p.replace(',', '')) for p in PRICE_RE.findall(body)]

            href = links[0]
            slots.append({
                'time': '%d:%02d %s' % (hour % 12 or 12, int(mm), meridiem),
                'spots': spots,
                'holes': '18',
                'price': min(prices) if prices else None,
                'bookUrl': href if href.startswith('http') else origin + href,
                '_min': minutes,
            })

        slots.sort(key=lambda s: s.pop('_min'))
        return slots

    async def fetch(self, course):
        host = course['host']
        days_ahead = int(course.get('daysAhead', 8))
        today = datetime.now()
        days = []

        for offset in range(days_ahead):
            date = today + timedelta(days=offset)
            label = date.strftime('%m/%d/%Y')
            try:
                html = self._fetch_day(host, date)
                days.append({'date': label, 'slots': self._parse(html, host)})
            except urllib.error.HTTPError as e:
                if e.code in (404, 403):      # past the booking window
                    days.append({'date': label, 'slots': []})
                else:
                    raise
            except urllib.error.URLError as e:
                logger.warning("%s %s: %s", course['name'], label, e)
                days.append({'date': label, 'slots': []})

        open_days = [d['date'] for d in days if d['slots']]
        return {
            'days': days,
            'window': {
                'min': open_days[0] if open_days else days[0]['date'],
                'max': open_days[-1] if open_days else days[-1]['date'],
            },
        }
