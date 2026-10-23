"""
Daily tee time search — checks available times for today + next 7 days
and sends a summary via iMessage.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

from tee_time_booker import TeeTimeBooker
from imessage_booker import send_imessage, _load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def search_all_dates():
    _load_dotenv()
    phone = os.environ.get('BOOKING_PHONE')
    if not phone:
        logger.error("BOOKING_PHONE not set in .env")
        return

    booker = TeeTimeBooker()
    today = datetime.now()

    # today + next 7 days; anything past the site's window is skipped for us
    dates = [(today + timedelta(days=o)).strftime('%m/%d/%Y') for o in range(8)]

    # One browser session for all dates — logs in once instead of per-date
    results_by_date = await booker.search_multiple_dates(dates, num_players=1)

    # Format message
    found = {d: t for d, t in results_by_date.items() if t}
    if not found:
        msg = "Golf search: No available tee times found for the next 8 days."
    else:
        lines = ["Available tee times:"]
        for date_str, times in found.items():
            label = datetime.strptime(date_str, '%m/%d/%Y').strftime('%a %m/%d')
            lines.append(f"\n{label}")
            for t in times:
                lines.append(f"  {t['time']} ({t['spots']} open)")
        msg = "\n".join(lines)

    logger.info(f"Sending summary:\n{msg}")
    try:
        send_imessage(phone, msg)
        logger.info("iMessage sent successfully")
    except Exception as e:
        logger.error(f"Failed to send iMessage: {e}")


if __name__ == '__main__':
    asyncio.run(search_all_dates())
