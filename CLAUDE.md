# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python 3 async browser automation tool that books tee times at Charleston Municipal Golf Course via the WebTrac booking system. Single-file application (`tee_time_booker.py`) with a JSON config file (`booking_config.json`).

## Setup & Run

```bash
# Install dependencies
pip install playwright --break-system-packages
python -m playwright install chromium

# Run (uses booking_config.json for settings)
python tee_time_booker.py
```

No build step, no test framework, no linter configured.

## Architecture

**`TeeTimeBooker` class** in `tee_time_booker.py` — the entire application:

- **`monitor_and_book()`** — Main loop: calculates target date from `days_ahead`, iterates through `preferred_times` in priority order, retries at `check_interval_minutes`. Default entry point in `main()`.
- **`book_tee_time(target_date, target_time)`** — Core booking flow: launches Playwright Chromium browser, navigates to WebTrac site, finds date input (tries multiple CSS selector fallbacks), selects time slot, fills user info form, optionally auto-submits. Takes screenshots at each step for debugging.
- **`wait_for_booking_window()`** — Sleeps until midnight for timed booking window releases.

**Booking target URL** is hardcoded in `__init__` (WebTrac golf module for Charleston SC).

**Two usage modes** controlled by editing `main()`:
1. Continuous monitoring: `await booker.monitor_and_book()` (default)
2. Immediate one-shot: `await booker.book_tee_time('2026-02-14', '08:00')`

## Configuration (`booking_config.json`)

- `user_info` — Player name, email, phone (auto-filled into booking form)
- `preferences.preferred_times` — Ordered list of desired times (tried in sequence)
- `preferences.days_ahead` — How far in advance to target
- `automation.auto_submit` — `false` pauses 60s for manual review; `true` clicks submit
- `automation.headless` — Run browser visibly or in background

## Key Patterns

- **Selector fallbacks**: Multiple CSS selectors tried in sequence for each form element (date inputs, time slots, player fields) to handle site variations.
- **Screenshot debugging**: `booking_page_1.png` through `booking_page_4.png`, `booking_confirmation.png`, and `booking_error.png` are saved during each booking attempt.
- **Async throughout**: Uses `playwright.async_api` and `asyncio`. All browser interactions are `await`ed.
