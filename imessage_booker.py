"""
iMessage-based booking value collection for the tee time booker.
Sends a prompt via iMessage, waits for a reply, parses booking details,
and returns them for use by the booking flow.
"""

import asyncio
import os
import re
import shutil
import sqlite3
import subprocess
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _load_dotenv():
    """Load .env file from the script directory if it exists"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())
    except FileNotFoundError:
        pass


def send_imessage(phone: str, message: str):
    """Send an iMessage via AppleScript."""
    escaped = message.replace('\\', '\\\\').replace('"', '\\"')
    script = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{phone}" of targetService
        send "{escaped}" to targetBuddy
    end tell
    '''
    result = subprocess.run(
        ['osascript', '-e', script],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to send iMessage: {result.stderr.strip()}")
    logger.info(f"Sent iMessage to {phone}")


def get_latest_reply(phone: str, since_timestamp: float) -> Optional[str]:
    """
    Read the latest inbound iMessage from `phone` received after `since_timestamp`.
    Copies chat.db to /tmp to avoid locking issues.
    Returns the message text, or None if no new message found.
    """
    chat_db = os.path.expanduser('~/Library/Messages/chat.db')
    tmp_db = '/tmp/chat_db_copy.sqlite'

    # Copy the WAL and SHM files too — recent messages live in the WAL
    # and won't be visible without them.
    shutil.copy2(chat_db, tmp_db)
    for suffix in ('-wal', '-shm'):
        src = chat_db + suffix
        if os.path.exists(src):
            shutil.copy2(src, tmp_db + suffix)

    # Apple's Core Data epoch is 2001-01-01 00:00:00 UTC
    # Convert Unix timestamp to Apple epoch (nanoseconds)
    apple_epoch_offset = 978307200
    apple_timestamp = (since_timestamp - apple_epoch_offset) * 1_000_000_000

    # Normalize phone: strip everything but digits, match last 10
    digits = re.sub(r'\D', '', phone)
    if len(digits) >= 10:
        digits = digits[-10:]
    phone_pattern = f'%{digits}'

    conn = sqlite3.connect(tmp_db)
    try:
        # Skip is_from_me filter — it can be inverted when both devices
        # share the same Apple ID.  Instead, exclude messages that look
        # like the prompt we sent (start with "Golf booker").
        cursor = conn.execute('''
            SELECT m.text, m.date
            FROM message m
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            JOIN chat c ON cmj.chat_id = c.ROWID
            WHERE m.date > ?
              AND c.chat_identifier LIKE ?
              AND m.text IS NOT NULL
              AND m.text NOT LIKE 'Golf booker%'
            ORDER BY m.date DESC
            LIMIT 1
        ''', (int(apple_timestamp), phone_pattern))
        row = cursor.fetchone()
        if row:
            logger.info(f"Found message: {row[0]!r} (date={row[1]})")
        else:
            logger.debug(f"No new messages after apple_ts={int(apple_timestamp)} for {phone_pattern}")
    finally:
        conn.close()
        for suffix in ('', '-wal', '-shm'):
            path = tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    if row and row[0]:
        return row[0].strip()
    return None


def parse_booking_request(text: str) -> dict:
    """
    Parse a natural-language booking request into structured data.

    Handles formats like:
      "tomorrow 2pm 1 player"
      "02/08 10:00 am 2 players"
      "saturday 7am"
      "2/14 3:30pm 4"

    Returns: {date: "MM/DD/YYYY", time: "HH:MM", players: int, search_only: bool}

    If the message contains "available", "what's", or "search", search_only=True.
    """
    text = text.strip().lower()
    today = datetime.now()
    search_keywords = ['available', "what's", 'whats', 'search', 'show', 'list', 'check']
    is_search = any(kw in text for kw in search_keywords)
    result = {'date': None, 'time': None, 'players': 1, 'search_only': is_search}

    # --- Parse date ---
    # "tomorrow"
    if 'tomorrow' in text:
        result['date'] = (today + timedelta(days=1)).strftime('%m/%d/%Y')
    # "today"
    elif 'today' in text:
        result['date'] = today.strftime('%m/%d/%Y')
    else:
        # Day of week: "monday", "tuesday", etc.
        days_of_week = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for i, day_name in enumerate(days_of_week):
            if day_name in text or day_name[:3] in text.split():
                current_dow = today.weekday()  # 0=Monday
                days_ahead = (i - current_dow) % 7
                if days_ahead == 0:
                    days_ahead = 7  # Next week if today
                result['date'] = (today + timedelta(days=days_ahead)).strftime('%m/%d/%Y')
                break

        # MM/DD or M/D format (with optional /YYYY)
        if not result['date']:
            date_match = re.search(r'(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?', text)
            if date_match:
                month = int(date_match.group(1))
                day = int(date_match.group(2))
                year = date_match.group(3)
                if year:
                    year = int(year)
                    if year < 100:
                        year += 2000
                else:
                    year = today.year
                result['date'] = f'{month:02d}/{day:02d}/{year}'

    # Default to tomorrow if no date parsed
    if not result['date']:
        result['date'] = (today + timedelta(days=1)).strftime('%m/%d/%Y')

    # --- Parse time ---
    # Matches: "2pm", "2:30pm", "14:00", "2:30 pm", "10 am"
    time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        period = time_match.group(3)
        if period == 'pm' and hour != 12:
            hour += 12
        elif period == 'am' and hour == 12:
            hour = 0
        result['time'] = f'{hour:02d}:{minute:02d}'
    else:
        # Try 24h format: "14:00"
        time_24_match = re.search(r'\b(\d{1,2}):(\d{2})\b', text)
        if time_24_match:
            hour = int(time_24_match.group(1))
            minute = int(time_24_match.group(2))
            if 0 <= hour <= 23:
                result['time'] = f'{hour:02d}:{minute:02d}'

    # If no time was given, treat as a search request
    if not result['time']:
        result['time'] = '08:00'
        result['search_only'] = True

    # --- Parse players ---
    players_match = re.search(r'(\d)\s*player', text)
    if players_match:
        result['players'] = int(players_match.group(1))
    else:
        # Standalone digit at end or near "player"
        digit_match = re.search(r'\b([1-4])\b', text)
        # Only use standalone digit if it's not part of the time or date
        if digit_match:
            # Check it's not the time hour or date component
            pos = digit_match.start()
            surrounding = text[max(0, pos-2):pos+3]
            if ':' not in surrounding and '/' not in surrounding and 'am' not in surrounding and 'pm' not in surrounding:
                result['players'] = int(digit_match.group(1))

    return result


async def prompt_for_booking(phone: str = None, poll_interval: int = 30, timeout: int = 600) -> Optional[dict]:
    """
    Send an iMessage prompt, wait for a reply, parse and return booking details.

    Args:
        phone: Phone number to message. Defaults to BOOKING_PHONE env var.
        poll_interval: Seconds between checking for replies (default 30).
        timeout: Max seconds to wait for a reply (default 600 = 10 min).

    Returns:
        Parsed booking dict {date, time, players} or None on timeout.
    """
    if not phone:
        phone = os.environ.get('BOOKING_PHONE', '')
    if not phone:
        raise ValueError("No phone number provided. Set BOOKING_PHONE in .env")

    prompt_message = (
        "Golf booker ready! Reply with:\n"
        "  Book: 'tomorrow 7am 1 player'\n"
        "  Search: 'what's available today'"
    )

    send_imessage(phone, prompt_message)
    sent_at = time.time()
    deadline = sent_at + timeout

    logger.info(f"Waiting up to {timeout}s for reply from {phone}...")

    while time.time() < deadline:
        await asyncio.sleep(poll_interval)
        reply = get_latest_reply(phone, sent_at)
        if reply:
            logger.info(f"Received reply: {reply}")
            booking = parse_booking_request(reply)
            logger.info(f"Parsed booking: {booking}")

            # Send confirmation
            hour = int(booking['time'].split(':')[0])
            minute = booking['time'].split(':')[1]
            period = 'AM' if hour < 12 else 'PM'
            display_hour = hour if hour <= 12 else hour - 12
            if display_hour == 0:
                display_hour = 12
            time_display = f"{display_hour}:{minute} {period}"

            if booking.get('search_only'):
                confirm_msg = f"Searching available tee times for {booking['date']}..."
            else:
                confirm_msg = (
                    f"Booking: {booking['date']} at {time_display} "
                    f"for {booking['players']} player(s). Starting now..."
                )
            send_imessage(phone, confirm_msg)
            return booking

    logger.warning("Timed out waiting for iMessage reply")
    send_imessage(phone, "Golf booker timed out waiting for your reply. Run again when ready.")
    return None


def send_booking_result(phone: str, success: bool, date: str = '', time_str: str = '',
                        no_availability: bool = False):
    """Send a post-booking result message via iMessage."""
    if no_availability:
        msg = f"No tee times available for {date} at {time_str}. Try a different date or time."
    elif success:
        msg = f"Booked! Tee time at {time_str} on {date} at Charleston Municipal."
    else:
        msg = f"Booking failed for {date} at {time_str}. Check screenshots for details."
    send_imessage(phone, msg)


# Standalone test
if __name__ == '__main__':
    _load_dotenv()
    phone = os.environ.get('BOOKING_PHONE', '')
    if not phone:
        print("Set BOOKING_PHONE in .env")
        exit(1)

    print(f"Testing iMessage flow with {phone}...")
    result = asyncio.run(prompt_for_booking(phone))
    if result:
        print(f"Booking details: {result}")
    else:
        print("No reply received")
