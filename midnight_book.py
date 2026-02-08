"""
One-shot midnight booker: sleeps until midnight, then books 02/17/2026 8am for 1 player.
Auto-submits the booking. Run with: nohup python3 midnight_book.py &
"""
import asyncio
from datetime import datetime, timedelta
from tee_time_booker import TeeTimeBooker
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TARGET_DATE = '02/17/2026'
TARGET_TIME = '08:00'
NUM_PLAYERS = 1


async def main():
    booker = TeeTimeBooker()

    # Override config for this run
    booker.config['automation']['auto_submit'] = True
    booker.config['automation']['headless'] = False
    booker.config['preferences']['num_players'] = NUM_PLAYERS

    # Sleep until 7am tomorrow
    now = datetime.now()
    target_run = (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    wait = (target_run - now).total_seconds()

    if wait > 0:
        logger.info(f"Sleeping until {target_run} ({wait:.0f}s / {wait/3600:.1f}h)")
        await asyncio.sleep(wait)

    logger.info(f"7am! Booking {TARGET_DATE} at {TARGET_TIME} for {NUM_PLAYERS} player(s)")

    try:
        booked = await booker.book_tee_time(TARGET_DATE, TARGET_TIME)
        if booked:
            logger.info("BOOKING SUCCESSFUL!")
        else:
            logger.warning("No availability found")
    except Exception as e:
        logger.error(f"Booking failed: {e}")

    # Send result via iMessage
    try:
        import os
        from imessage_booker import send_booking_result
        phone = os.environ.get('BOOKING_PHONE', '')
        if phone:
            send_booking_result(phone, booked, TARGET_DATE, TARGET_TIME,
                                no_availability=(not booked))
    except Exception:
        pass


if __name__ == '__main__':
    asyncio.run(main())
