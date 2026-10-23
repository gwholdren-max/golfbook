"""
Daily tee time card — renders today's remaining times for one course as a
PNG and sends it by iMessage or email.

    python3 daily_card.py                      # default course, send it
    python3 daily_card.py --course stono-ferry
    python3 daily_card.py --tomorrow           # tomorrow instead of today
    python3 daily_card.py --save-only          # just write the PNG
    python3 daily_card.py --email you@example.com

Scrapes the course directly rather than reading data/tee_times.json, so it
works for a course the collector has paused (Charleston Muni is paused
because WebTrac needs a browser — once a day is a different proposition
from every five minutes).

Delivery, in order of what's configured:
  --email / CARD_EMAIL   SMTP, works anywhere
  --phone / CARD_PHONE   iMessage, macOS only
"""

import argparse
import asyncio
import json
import logging
import os
import smtplib
import ssl
import subprocess
import sys
from datetime import datetime, timedelta
from email.message import EmailMessage

import adapters
from tee_time_booker import TeeTimeBooker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COURSES_FILE = os.path.join(BASE_DIR, 'courses.json')
OUT_DIR = os.path.join(BASE_DIR, 'data')

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('daily_card')


# ---------------------------------------------------------------- data

def load_course(course_id):
    with open(COURSES_FILE) as f:
        courses = json.load(f)['courses']
    for c in courses:
        if c['id'] == course_id:
            return c
    raise SystemExit("No course '%s' in courses.json. Available: %s"
                     % (course_id, ', '.join(c['id'] for c in courses)))


def minutes_of(label):
    """'7:38 am' -> minutes since midnight."""
    try:
        clock, meridiem = label.strip().rsplit(' ', 1)
        hh, mm = clock.split(':')
        hour = int(hh) % 12
        if meridiem.lower().startswith('p'):
            hour += 12
        return hour * 60 + int(mm)
    except ValueError:
        return 0


async def slots_for_day(course, offset):
    """Open times on the day `offset` days from today."""
    adapter = adapters.get_adapter(course['platform'])
    if not adapter:
        raise SystemExit("No adapter for platform '%s'" % course['platform'])

    # Adapters always walk forward from today, so ask for enough days
    probe = dict(course)
    probe['daysAhead'] = offset + 1
    probe.setdefault('enabled', True)
    result = await adapter.fetch(probe)

    target = (datetime.now() + timedelta(days=offset)).strftime('%m/%d/%Y')
    for day in result.get('days', []):
        if day['date'] == target:
            return day['slots']
    return []


# ---------------------------------------------------------------- render

CARD_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600&family=Barlow+Condensed:wght@600;700&display=swap">
<style>
  :root {
    --paper:#F4F4F1; --surface:#FFF; --ink:#16211A; --muted:#5C6B60;
    --faint:#8A968D; --line:#DCDED6; --accent:#2F6B4A;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; width:760px; background:var(--paper); color:var(--ink);
    font-family:Archivo,"Helvetica Neue",Arial,sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  .card { padding:36px 40px 32px; }
  .kicker {
    font-family:"Barlow Condensed","Arial Narrow",sans-serif; font-weight:600;
    text-transform:uppercase; letter-spacing:.1em; font-size:14px; color:var(--faint);
  }
  h1 {
    font-family:"Barlow Condensed","Arial Narrow",sans-serif; font-weight:700;
    font-size:52px; line-height:1; margin:6px 0 0; letter-spacing:-.005em;
  }
  .when { margin-top:10px; font-size:17px; color:var(--muted); }
  .rule { height:3px; background:var(--ink); margin:20px 0 4px; }

  .group { margin-top:24px; }
  .glabel {
    font-family:"Barlow Condensed","Arial Narrow",sans-serif; font-weight:600;
    text-transform:uppercase; letter-spacing:.09em; font-size:14px;
    color:var(--faint); display:block; margin-bottom:12px;
  }
  .grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }
  .slot {
    background:var(--surface); border:1px solid var(--line);
    border-radius:5px; padding:13px 14px 12px;
  }
  .slot.four { border-left:4px solid var(--accent); }
  .t { font-size:25px; font-weight:600; line-height:1.05; font-variant-numeric:tabular-nums; }
  .s { margin-top:7px; font-size:13px; color:var(--muted); display:flex; align-items:center; gap:7px; }
  .pips { display:inline-flex; gap:3px; }
  .pip { width:8px; height:8px; border-radius:50%; background:transparent; border:1.5px solid var(--line); }
  .pip.on { background:var(--accent); border-color:var(--accent); }

  .none { padding:46px 0; text-align:center; color:var(--muted); font-size:19px; }
  .none b { display:block; color:var(--ink); font-size:25px; margin-bottom:7px; font-weight:600; }

  .foot {
    margin-top:30px; padding-top:16px; border-top:1px solid var(--line);
    font-size:13px; color:var(--faint); display:flex; justify-content:space-between;
  }
</style></head>
<body><div class="card">
  <div class="kicker">__AREA__</div>
  <h1>__NAME__</h1>
  <div class="when">__WHEN__</div>
  <div class="rule"></div>
  __BODY__
  <div class="foot"><span>__COUNT__</span><span>as of __STAMP__</span></div>
</div></body></html>"""


def build_html(course, slots, now, is_today=True):
    groups = [('Morning', 0, 12 * 60), ('Midday', 12 * 60, 16 * 60),
              ('Afternoon', 16 * 60, 24 * 60)]
    chunks = []
    for label, lo, hi in groups:
        picked = [s for s in slots if lo <= minutes_of(s['time']) < hi]
        if not picked:
            continue
        cells = []
        for s in picked:
            spots = int(s.get('spots') or 0)
            pips = ''.join('<span class="pip%s"></span>' % (' on' if i < spots else '')
                           for i in range(4))
            price = s.get('price')
            extra = ('  &middot;  $%d' % round(price)) if isinstance(price, (int, float)) else ''
            cells.append(
                '<div class="slot%s"><div class="t">%s</div>'
                '<div class="s"><span class="pips">%s</span>'
                '<span>%d open%s</span></div></div>'
                % (' four' if spots >= 4 else '',
                   s['time'].replace(' am', 'am').replace(' pm', 'pm'),
                   pips, spots, extra))
        chunks.append('<div class="group"><span class="glabel">%s</span>'
                      '<div class="grid">%s</div></div>' % (label, ''.join(cells)))

    if not chunks:
        body = ('<div class="none"><b>%s</b>%s</div>'
                % ('Nothing left today' if is_today else 'Fully booked',
                   'No open tee times remaining.' if is_today
                   else 'No open tee times on this day.'))
        count = 'No times available'
    else:
        body = ''.join(chunks)
        count = '%d tee time%s open' % (len(slots), '' if len(slots) == 1 else 's')

    return (CARD_HTML
            .replace('__AREA__', course.get('area', '') or 'Charleston')
            .replace('__NAME__', course['name'])
            .replace('__WHEN__', now.strftime('%A, %B %-d'))
            .replace('__BODY__', body)
            .replace('__COUNT__', count)
            .replace('__STAMP__', now.strftime('%-I:%M %p').lower()))


async def render_png(html, path):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={'width': 760, 'height': 900},
            device_scale_factor=2)          # retina, so it is crisp on a phone
        await page.set_content(html, wait_until='networkidle')
        await page.wait_for_timeout(400)    # let the webfont settle
        el = await page.query_selector('.card')
        await el.screenshot(path=path)
        await browser.close()
    return path


# ---------------------------------------------------------------- send

def send_imessage_image(phone, image_path, caption):
    """iMessage the PNG, then the caption. macOS only."""
    script = '''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "%s" of targetService
        send POSIX file "%s" to targetBuddy
        send "%s" to targetBuddy
    end tell
    ''' % (phone, image_path, caption.replace('\\', '\\\\').replace('"', '\\"'))
    result = subprocess.run(['osascript', '-e', script],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError('iMessage failed: %s' % result.stderr.strip())
    logger.info('Sent iMessage to %s', phone)


def send_email_image(to_addr, image_path, subject, caption):
    host = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
    port = int(os.environ.get('SMTP_PORT', '587'))
    user = os.environ.get('SMTP_USER')
    password = os.environ.get('SMTP_PASS')
    if not (user and password):
        raise SystemExit('Set SMTP_USER and SMTP_PASS in .env to send email. '
                         'For Gmail that means an App Password, not your '
                         'account password.')

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = user
    msg['To'] = to_addr
    msg.set_content(caption)
    with open(image_path, 'rb') as f:
        msg.add_attachment(f.read(), maintype='image', subtype='png',
                           filename=os.path.basename(image_path))

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)
    logger.info('Emailed %s', to_addr)


# ---------------------------------------------------------------- main

async def run(args):
    TeeTimeBooker()._load_dotenv()          # pull .env into the environment

    course = load_course(args.course)
    now = datetime.now()
    offset = 1 if args.tomorrow else 0
    target = now + timedelta(days=offset)

    logger.info('Fetching %s for %s...', course['name'], target.strftime('%m/%d'))
    slots = await slots_for_day(course, offset)

    # Drop times already gone — but only when the card is for today
    cutoff = (now.hour * 60 + now.minute) if offset == 0 else 0
    upcoming = [s for s in slots if minutes_of(s['time']) >= cutoff]
    upcoming.sort(key=lambda s: minutes_of(s['time']))
    logger.info('%d of %d times showing', len(upcoming), len(slots))

    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    out = os.path.join(OUT_DIR, 'card-%s-%s.png'
                       % (course['id'], target.strftime('%Y%m%d')))
    await render_png(build_html(course, upcoming, target, offset == 0), out)
    logger.info('Wrote %s', out)

    caption = ('%s %s: %s'
               % (course['name'],
                  'tomorrow' if offset else 'today',
                  ', '.join(s['time'] for s in upcoming[:4]) + (
                      ' and %d more' % (len(upcoming) - 4) if len(upcoming) > 4 else '')
                  if upcoming else ('nothing open' if offset else 'nothing left today')))

    if args.save_only:
        print(out)
        return

    email = args.email or os.environ.get('CARD_EMAIL')
    phone = args.phone or os.environ.get('CARD_PHONE') or os.environ.get('BOOKING_PHONE')

    if email:
        send_email_image(email, out, '%s tee times' % course['name'], caption)
    elif phone and sys.platform == 'darwin':
        send_imessage_image(phone, out, caption)
    else:
        raise SystemExit('Nowhere to send it. Pass --email/--phone, or set '
                         'CARD_EMAIL/CARD_PHONE in .env. (iMessage is macOS only.)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--course', default='charleston-muni')
    parser.add_argument('--email')
    parser.add_argument('--phone')
    parser.add_argument('--tomorrow', action='store_true',
                        help="tomorrow's card instead of today's")
    parser.add_argument('--save-only', action='store_true',
                        help='render the PNG and print its path, send nothing')
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
