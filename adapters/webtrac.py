"""
Vermont Systems WebTrac adapter.

Used by municipal parks departments — Charleston Municipal runs on it.
Wraps TeeTimeBooker, which already handles the site's quirks: hidden Vue
form controls, the ambiguous "Search" button, and mobile-column-header
text pollution in the results table.
"""

import logging
from datetime import datetime, timedelta

from tee_time_booker import TeeTimeBooker

logger = logging.getLogger(__name__)


class WebTracAdapter:
    name = 'webtrac'

    def __init__(self):
        self._bookers = {}

    def _booker(self, course):
        """One TeeTimeBooker per course — they differ by booking URL."""
        key = course['id']
        if key not in self._bookers:
            booker = TeeTimeBooker()
            if course.get('bookingUrl'):
                booker.booking_url = course['bookingUrl']
            if not course.get('login'):
                # Reading the tee sheet needs no account, and scraping on a
                # schedule under a real login risks getting it flagged.
                # Blanking the credentials makes TeeTimeBooker.login() a no-op.
                booker.config['user_info']['username'] = ''
                booker.config['user_info']['password'] = ''
                booker.config['user_info']['email'] = ''
            self._bookers[key] = booker
        return self._bookers[key]

    async def fetch(self, course):
        booker = self._booker(course)
        days_ahead = int(course.get('daysAhead', 8))
        players = int(course.get('numPlayers', 1))

        today = datetime.now()
        dates = [(today + timedelta(days=i)).strftime('%m/%d/%Y')
                 for i in range(days_ahead)]

        results = await booker.search_multiple_dates(dates, num_players=players)

        days = []
        for date in dates:
            slots = []
            for slot in results.get(date, []):
                try:
                    spots = int(slot.get('spots') or 0)
                except ValueError:
                    spots = 0
                slots.append({
                    'time': slot.get('time', ''),
                    'spots': spots,
                    'holes': slot.get('holes', ''),
                    'bookUrl': slot.get('href', ''),
                })
            days.append({'date': date, 'slots': slots})

        return {
            'days': days,
            'window': {'min': dates[0], 'max': dates[-1]},
        }
