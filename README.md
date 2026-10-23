# Charleston Municipal Golf Course - Tee Time Auto-Booker

Automatically books tee times at Charleston Municipal Golf Course when they become available.

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install playwright --break-system-packages
python -m playwright install chromium
```

### 2. Configure Your Settings

Edit `booking_config.json`:

```json
{
  "user_info": {
    "first_name": "Your First Name",
    "last_name": "Your Last Name",
    "email": "your.email@example.com",
    "phone": "843-555-1234"
  },
  "preferences": {
    "preferred_times": ["07:00", "07:30", "08:00"],
    "days_ahead": 7,
    "num_players": 2
  },
  "automation": {
    "check_interval_minutes": 5,
    "auto_submit": false,
    "headless": false
  }
}
```

**Key Settings:**
- `preferred_times`: List your desired tee times (tries them in order)
- `days_ahead`: How many days in advance to book (typically 7)
- `auto_submit`: Set to `true` to automatically complete bookings (starts as false for safety)
- `headless`: Set to `true` to run browser in background

### 3. Run the Booker

**Test Mode (Manual Submit):**
```bash
python tee_time_booker.py
```
The browser will open, navigate to the booking site, fill everything out, and pause before submitting so you can review.

**Auto Mode (Hands-Free):**
1. Set `"auto_submit": true` in config
2. Run: `python tee_time_booker.py`
3. The script will automatically book when times become available

## 📋 How It Works

1. **Monitors** the Charleston Municipal booking site at your specified interval
2. **Searches** for your target date (X days ahead)
3. **Finds** your preferred tee times in priority order
4. **Books** the first available time that matches your preferences
5. **Fills** all your information automatically
6. **Submits** (if auto_submit is enabled) or pauses for manual review

## 🎯 Usage Examples

**Book immediately for a specific date/time:**
Edit the `main()` function in `tee_time_booker.py`:
```python
async def main():
    booker = TeeTimeBooker()
    await booker.book_tee_time('2026-02-14', '08:00')
```

**Monitor continuously:**
Just run the script - it will keep checking until it successfully books.

## 📸 Debugging

The script saves screenshots at each step:
- `booking_page_1.png` - Initial page load
- `booking_page_2.png` - After date selection
- `booking_page_3.png` - After time selection
- `booking_page_4.png` - Form filled out
- `booking_confirmation.png` - Final confirmation
- `booking_error.png` - If an error occurs

Check these if something goes wrong!

## ⚠️ Important Notes

- **Test first**: Run with `auto_submit: false` to see what it does
- **Timing matters**: Charleston Municipal likely releases tee times at midnight or early morning
- **Stay legal**: This is automation for personal use, not scalping/reselling
- **One at a time**: Don't run multiple instances or you might double-book

## 🛠️ Troubleshooting

**Browser won't open:**
```bash
python -m playwright install chromium
```

**Can't find form fields:**
- Check the screenshots to see what's happening
- The site structure might have changed
- Run with `headless: false` to watch it work

**Gets stuck:**
- Increase `check_interval_minutes` to avoid rate limiting
- Check if the site requires login/account

## 💡 Pro Tips

1. Set your script to run just before midnight when new times release
2. List multiple preferred times in priority order
3. Use `headless: true` once you've verified it works
4. Keep logs to see when times become available

## Tee time website

A local web server that shows Charleston Muni's live availability. Two
processes: `collector.py` scrapes on a loop, `server.py` serves the site.

```bash
# one-off: pull current availability into data/tee_times.json
python3 collector.py --once

# the two long-running processes
python3 collector.py --interval 300     # scrape every 5 minutes
python3 server.py --host 0.0.0.0        # serve on http://<machine>:8080
```

`--host 0.0.0.0` makes it reachable from other devices on the network;
leave it off to bind localhost only.

**Routes**

| Route | What it returns |
|---|---|
| `/` | the site |
| `/api/tee-times` | collector output as JSON |
| `/healthz` | `503` once data is older than 30 minutes — point a watchdog here |

**Notes for the dedicated machine**

- The scraper needs a real (non-headless) browser; Cloudflare blocks headless
  Chromium on WebTrac. On a machine with no display, run the collector under
  `xvfb-run`.
- A failed scrape keeps the previous data rather than blanking the site, and
  the page shows a warning banner when it's serving a stale read.
- `data/tee_times.json` is written atomically, so the server never reads a
  half-written file.

**Adding another course** — `courses.json` maps courses to adapters in
`adapters/`. A course running a platform that already has an adapter is a
config entry; a new platform is a new file in `adapters/` plus one line in
`adapters/__init__.py`.

### Running it on a Raspberry Pi

A Pi makes a good home for this: it's always on, and because it's headless
there's no window for the WebTrac adapter to pop up — so Charleston Muni
can run there even though it needs a real browser.

Use **64-bit** Raspberry Pi OS (Playwright only ships an ARM64 Chromium) on a
Pi 4 or 5 with 4GB. Boot from a USB SSD if you can; cheap SD cards wear out
under a write every five minutes.

```bash
git clone https://github.com/gwholdren-max/golfbook.git ~/golfbook
cd ~/golfbook && ./deploy/setup-pi.sh
```

That installs dependencies, Chromium, and `cloudflared`, then registers three
systemd services that start on boot and restart on failure:

| Service | Does |
|---|---|
| `teetime-collector@<user>` | scrapes every 5 min, under `xvfb` |
| `teetime-server@<user>` | serves the site on :8080 |
| `teetime-tunnel@<user>` | exposes it publicly via Cloudflare |

They're systemd *template* units, so `%i` resolves to your username and the
paths work without editing anything.

```bash
systemctl status 'teetime-*'
journalctl -u teetime-collector@$USER -f
curl -s localhost:8080/healthz
```

Once it's on the Pi, turn Muni back on — set `"enabled": true` in
`courses.json`. No restart needed; the collector re-reads the config each pass.

A quick tunnel hands out a new `*.trycloudflare.com` URL every time it
restarts. For an address that survives reboots, create a named tunnel and
swap the `ExecStart` line in `deploy/teetime-tunnel@.service`.
