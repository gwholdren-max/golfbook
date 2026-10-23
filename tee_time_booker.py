"""
Charleston Municipal Golf Course - Tee Time Auto-Booker
Automatically books tee times at specified dates/times using browser automation
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TeeTimeBooker:
    def __init__(self, config_file='booking_config.json'):
        """Initialize the tee time booker with configuration"""
        self.config = self.load_config(config_file)
        self.booking_url = "https://sccharlestonweb.myvscloud.com/webtrac/web/search.html?module=GR&Search=no&interfaceparameter=webtrac_golf"
        
    def load_config(self, config_file):
        """Load booking configuration from JSON file, with .env overrides"""
        self._load_dotenv()
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
        except FileNotFoundError:
            logger.warning(f"Config file {config_file} not found, using defaults")
            config = self.get_default_config()

        # Override user_info with environment variables if set
        env_map = {
            'first_name': 'BOOKING_FIRST_NAME',
            'last_name': 'BOOKING_LAST_NAME',
            'email': 'BOOKING_EMAIL',
            'phone': 'BOOKING_PHONE',
            'password': 'BOOKING_PASSWORD',
            'username': 'BOOKING_USERNAME',
        }
        for key, env_var in env_map.items():
            value = os.environ.get(env_var)
            if value:
                config['user_info'][key] = value

        return config

    def _load_dotenv(self):
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
    
    def get_default_config(self):
        """Return default configuration"""
        return {
            "user_info": {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john.doe@example.com",
                "phone": "555-123-4567"
            },
            "preferences": {
                "preferred_times": ["07:00", "07:30", "08:00"],
                "days_ahead": 7,  # How many days in advance to book
                "num_players": 2,
                "course": "Charleston Municipal"
            },
            "automation": {
                "check_interval_minutes": 5,
                "auto_submit": False,  # Set to True to auto-submit bookings
                "headless": False  # Set to True to run browser in background
            }
        }
    
    # --- Shared WebTrac page helpers -------------------------------------
    # The search form controls (#begindate, #numberofplayers) are hidden Vue
    # components, so Playwright's fill()/select_option() can't touch them —
    # everything goes through the native value setter plus input/change events.

    @staticmethod
    def _format_time_12h(target_time):
        """Convert 'HH:MM' (24h) to the site's 'hh:mm am/pm' format"""
        hour = int(target_time.split(':')[0])
        minute = target_time.split(':')[1]
        period = 'am' if hour < 12 else 'pm'
        display_hour = hour if hour <= 12 else hour - 12
        if display_hour == 0:
            display_hour = 12
        return f"{display_hour:02d}:{minute} {period}"

    @staticmethod
    def _in_window(target_date, min_date, max_date):
        """True if target_date (MM/DD/YYYY) falls within [min_date, max_date]"""
        fmt = '%m/%d/%Y'
        try:
            target = datetime.strptime(target_date, fmt)
            return (datetime.strptime(min_date, fmt) <= target
                    <= datetime.strptime(max_date, fmt))
        except ValueError:
            return True  # can't parse it — let the search run anyway

    async def get_booking_window(self, page):
        """
        Read the bookable date range straight off the date picker.
        The site publishes it as data-rule-mindate/data-rule-maxdate.

        Returns (min_date, max_date) as MM/DD/YYYY strings, or (None, None).
        """
        window = await page.evaluate('''() => {
            const d = document.querySelector('#begindate');
            if (!d) return null;
            return {
                min: d.getAttribute('data-rule-mindate') || d.getAttribute('data-datepicker-min-date'),
                max: d.getAttribute('data-rule-maxdate') || d.getAttribute('data-datepicker-max-date')
            };
        }''')
        if not window:
            return (None, None)
        return (window.get('min'), window.get('max'))

    async def set_search_criteria(self, page, target_date, num_players, begin_time='07:00'):
        """Fill the search form (players, date, begin time) via the native setters"""
        result = await page.evaluate('''({date, players, time}) => {
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            const out = {players: false, date: false, time: false};

            const sel = document.querySelector('#numberofplayers, select[name="numberofplayers"]');
            if (sel) {
                for (const o of sel.options) {
                    if (o.text.trim() === players || o.value === players) {
                        sel.value = o.value;
                        sel.dispatchEvent(new Event('input', {bubbles: true}));
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                        out.players = true;
                    }
                }
            }

            const setInput = (el, value) => {
                setter.call(el, value);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            };

            const d = document.querySelector('#begindate, input[name="begindate"]');
            if (d) { setInput(d, date); out.date = true; }

            const t = document.querySelector('#begintime, input[name="begintime"]');
            if (t) { setInput(t, time); out.time = true; }

            // The Vue widgets render their own visible mirror inputs
            // (id like "begintime_vm_3_input"). Keep them in sync so the
            // widget doesn't write a stale value back on submit.
            document.querySelectorAll('input[id*="_vm_"][id$="_input"]').forEach(m => {
                if (/^\\d{2}\\/\\d{2}\\/\\d{4}$/.test(m.value)) setInput(m, date);
                else if (/^\\d{1,2}:\\d{2}\\s*[ap]m$/i.test(m.value)) setInput(m, time);
            });

            return out;
        }''', {'date': target_date, 'players': str(num_players),
               'time': self._format_time_12h(begin_time)})

        if not result.get('date'):
            logger.warning("Could not find the #begindate input")
        if not result.get('players'):
            logger.warning("Could not set the number of players")
        return result

    async def click_search(self, page, timeout=15000):
        """
        Submit the search form.

        The page has several elements whose text is exactly "Search" — the
        first in DOM order is the header nav dropdown (a <button type=submit>
        that only opens a menu). Clicking that is a no-op for the search, so
        target the sidebar button by id instead.
        """
        for selector in ('#grwebsearch_buttonsearch',
                         '#grwebsearch_noresultsbutton',
                         'button.filter-group-search'):
            btn = await page.query_selector(selector)
            if btn:
                await btn.click()
                logger.info(f"Clicked search button ({selector})")
                break
        else:
            logger.warning("Could not find the sidebar Search button")
            return False

        # Submitting the form is a full page navigation, so wait on the load
        # state rather than a results selector — a legitimately empty day
        # ("Your search did not return any matching results") would otherwise
        # burn the whole selector timeout.
        try:
            await page.wait_for_load_state('networkidle', timeout=timeout)
        except Exception:
            logger.info("Page did not reach networkidle before timeout")
        await page.wait_for_timeout(500)
        return True

    async def scrape_results(self, page):
        """
        Scrape the results table into [{time, date, holes, course, spots, href}].

        Each <td> is prefixed with a hidden "mobile-column-header" span
        (so a cell reads "Time 7:38 am"); that span is stripped here, and
        columns are matched by header name rather than fixed indexes.
        """
        return await page.evaluate('''() => {
            const cellText = (cell) => {
                const clone = cell.cloneNode(true);
                clone.querySelectorAll('.mobile-column-header').forEach(s => s.remove());
                return clone.textContent.replace(/\\s+/g, ' ').trim();
            };

            // Map column name -> index from the header row
            const headerRow = document.querySelector('tr:has(th)') ||
                              document.querySelector('thead tr');
            const headers = headerRow
                ? Array.from(headerRow.querySelectorAll('th,td')).map(c => cellText(c).toLowerCase())
                : [];
            const col = (name, fallback) => {
                const i = headers.indexOf(name);
                return i === -1 ? fallback : i;
            };
            const iTime = col('time', 1), iDate = col('date', 2),
                  iHoles = col('holes', 3), iCourse = col('course', 4),
                  iSlots = col('open slots', 5);

            const times = [];
            for (const row of document.querySelectorAll('tr')) {
                const btn = row.querySelector('a.cart-button');
                if (!btn) continue;
                if (btn.classList.contains('error')) continue;  // Unavailable
                const cells = Array.from(row.querySelectorAll('td')).map(cellText);
                times.push({
                    time: cells[iTime] || '',
                    date: cells[iDate] || '',
                    holes: cells[iHoles] || '',
                    course: cells[iCourse] || '',
                    spots: cells[iSlots] || '0',
                    href: btn.href || ''
                });
            }
            return times;
        }''')

    async def login(self, page):
        """Log in so cart buttons show as Available rather than Unavailable"""
        password = self.config['user_info'].get('password', '')
        username = (self.config['user_info'].get('username', '')
                    or self.config['user_info'].get('email', ''))
        if not (username and password):
            return False

        sign_in_link = await page.query_selector(
            'a:has-text("SIGN IN"), a:has-text("Sign In"), a:has-text("Login")')
        if sign_in_link:
            await sign_in_link.click()
            await page.wait_for_timeout(2000)

        if not await page.query_selector('input[type="password"]'):
            return False

        logger.info("Logging in before search...")
        username_input = await page.query_selector(
            'input[name*="user"], input[id*="user"], input[type="text"]')
        if username_input:
            await username_input.fill(username)
        pass_input = await page.query_selector('input[type="password"]')
        if pass_input:
            await pass_input.fill(password)
        login_btn = await page.query_selector('button:has-text("Login"), input[type="submit"]')
        if login_btn:
            await login_btn.click()
            await page.wait_for_timeout(3000)

        # Handle "Active Session Alert"
        continue_btn = await page.query_selector(
            'button:has-text("Continue with Login"), a:has-text("Continue with Login")')
        if continue_btn:
            logger.info("Active session alert, clicking Continue with Login...")
            await continue_btn.click()
            await page.wait_for_timeout(3000)

        await page.goto(self.booking_url, wait_until='networkidle')
        await page.wait_for_timeout(2000)
        return True

    async def wait_for_booking_window(self):
        """Wait until the booking window opens (usually midnight or 6 AM)"""
        # Adjust this based on when Charleston Municipal releases new tee times
        booking_hour = 0  # Midnight
        booking_minute = 0
        
        now = datetime.now()
        next_release = now.replace(hour=booking_hour, minute=booking_minute, second=0, microsecond=0)
        
        if now >= next_release:
            next_release += timedelta(days=1)
        
        wait_seconds = (next_release - now).total_seconds()
        logger.info(f"Waiting until {next_release.strftime('%Y-%m-%d %H:%M:%S')} to check for tee times...")
        logger.info(f"Time until check: {wait_seconds/3600:.2f} hours")
        
        await asyncio.sleep(wait_seconds)
    
    async def book_tee_time(self, target_date, target_time):
        """
        Main booking function - navigates site and books tee time

        Args:
            target_date: Date to book (MM/DD/YYYY format)
            target_time: Time to book (HH:MM format, 24h)
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.config['automation']['headless']
            )
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()

            try:
                logger.info("Navigating to Charleston Municipal booking site...")
                await page.goto(self.booking_url, wait_until='networkidle')
                await page.wait_for_timeout(2000)

                # Login first so cart buttons show as available
                await self.login(page)

                await page.screenshot(path='booking_page_1.png')
                logger.info("Screenshot saved: booking_page_1.png")

                # --- Step 1: Set search filters ---
                min_date, max_date = await self.get_booking_window(page)
                if min_date and max_date:
                    logger.info(f"Site booking window: {min_date} - {max_date}")
                    if not self._in_window(target_date, min_date, max_date):
                        logger.warning(
                            f"{target_date} is outside the booking window "
                            f"({min_date} - {max_date})")
                        return False

                players = self.config['preferences']['num_players']
                await self.set_search_criteria(page, target_date, players, target_time)
                logger.info(
                    f"Searching {target_date} from {self._format_time_12h(target_time)} "
                    f"for {players} player(s)")

                await page.screenshot(path='booking_page_2.png')
                await page.wait_for_timeout(500)

                # --- Step 2: Click Search (sidebar button, not the nav menu) ---
                await self.click_search(page)

                await page.screenshot(path='booking_page_3.png')

                # --- Step 3: Find and click on an available tee time result ---
                logger.info("Looking for available tee times...")

                # Debug: log the result table HTML structure
                table_debug = await page.evaluate('''() => {
                    const rows = document.querySelectorAll('tr');
                    const info = [];
                    for (const row of rows) {
                        const firstCell = row.querySelector('td');
                        if (firstCell) {
                            const link = firstCell.querySelector('a');
                            info.push({
                                text: row.textContent.replace(/\\s+/g, ' ').trim().substring(0, 80),
                                firstCellHTML: firstCell.innerHTML.substring(0, 200),
                                linkHref: link ? link.href : null
                            });
                        }
                    }
                    return info;
                }''')
                for row in table_debug:
                    logger.info(f"Row: {row.get('text', '')} | Link: {row.get('linkHref', 'none')} | HTML: {row.get('firstCellHTML', '')[:100]}")

                # Use Playwright click on the first link inside a result row (the green icon)
                clicked_result = await page.evaluate('''() => {
                    const rows = document.querySelectorAll('tr');
                    for (const row of rows) {
                        const text = row.textContent || '';
                        if (text.includes('Time') && text.includes('Holes') && text.includes('Course')) continue;
                        if (text.includes('Available')) {
                            const link = row.querySelector('td a');
                            if (link) {
                                return {found: true, href: link.href, text: text.replace(/\\s+/g, ' ').trim().substring(0, 100)};
                            }
                        }
                    }
                    return {found: false};
                }''')

                if clicked_result.get('found'):
                    logger.info(f"Found tee time: {clicked_result['text']}")

                    # Debug: log all cart buttons and their classes to find the "Available" one
                    btn_info = await page.evaluate('''() => {
                        const buttons = document.querySelectorAll('a.cart-button');
                        return Array.from(buttons).map((b, i) => ({
                            index: i,
                            classes: b.className,
                            title: b.title || b.getAttribute('data-original-title') || '',
                            ariaLabel: b.getAttribute('aria-label') || '',
                            text: b.textContent.trim(),
                            parentRow: b.closest('tr') ? b.closest('tr').textContent.replace(/\\s+/g, ' ').trim().substring(0, 60) : ''
                        }));
                    }''')
                    for btn in btn_info:
                        logger.info(f"Cart button[{btn['index']}]: classes='{btn['classes']}' title='{btn['title']}' aria='{btn['ariaLabel']}' row='{btn['parentRow']}'")

                    # Click the Available cart button (has "success" class, not "error")
                    cart_btn = await page.query_selector('a.cart-button:not(.error)')
                    if not cart_btn:
                        # Try finding by success class
                        cart_btn = await page.query_selector('a.cart-button.success')
                    if not cart_btn:
                        # Last resort: find any cart button with "Available" in its attributes
                        cart_btn = await page.query_selector('a.cart-button[title*="Available"], a.cart-button[aria-label*="Available"]')

                    if cart_btn:
                        await cart_btn.click()
                        logger.info("Clicked available cart button via Playwright")
                    else:
                        logger.warning("Could not find an available cart button, trying first one")
                        await page.click('tr:has-text("Available") a.cart-button')
                    await page.wait_for_timeout(3000)

                if clicked_result.get('found'):
                    logger.info(f"Clicked tee time: {clicked_result['text']}")
                    await page.wait_for_timeout(3000)
                else:
                    logger.warning("No available tee times found in results")
                    await browser.close()
                    return False

                # Handle login page if redirected after clicking tee time
                if 'login' in page.url.lower() or await page.query_selector('input[type="password"]'):
                    logger.info("Login page detected, signing in...")
                    username_input = await page.query_selector('input[name*="user"], input[id*="user"], input[type="text"]')
                    if username_input:
                        await username_input.fill(username)
                    pass_input = await page.query_selector('input[type="password"]')
                    if pass_input:
                        await pass_input.fill(password)
                    login_btn = await page.query_selector('button:has-text("Login"), input[type="submit"]')
                    if login_btn:
                        await login_btn.click()
                        await page.wait_for_timeout(3000)

                    # Handle "Active Session Alert" - click "Continue with Login"
                    continue_btn = await page.query_selector('button:has-text("Continue with Login"), a:has-text("Continue with Login")')
                    if continue_btn:
                        logger.info("Active session alert detected, clicking Continue with Login...")
                        await continue_btn.click()
                        await page.wait_for_timeout(3000)

                    await page.screenshot(path='booking_page_after_login2.png')
                    logger.info("Screenshot saved: booking_page_after_login2.png")

                    # After login, go back to search and re-find the tee time
                    logger.info("Logged in, navigating back to search for tee time...")
                    await page.goto(self.booking_url, wait_until='networkidle')
                    await page.wait_for_timeout(2000)

                    # Re-set filters
                    await page.evaluate(f'''(target) => {{
                        const selects = document.querySelectorAll('select');
                        for (const s of selects) {{
                            const opts = Array.from(s.options).map(o => o.text.trim());
                            if (opts.some(o => /^[1-4]$/.test(o))) {{
                                for (const o of s.options) {{
                                    if (o.text.trim() === target || o.value === target) {{
                                        s.value = o.value;
                                        s.dispatchEvent(new Event('change', {{bubbles: true}}));
                                    }}
                                }}
                            }}
                        }}
                    }}''', players)

                    await page.evaluate(f'''(target) => {{
                        const inputs = document.querySelectorAll('input');
                        for (const inp of inputs) {{
                            if (inp.type === 'date' || inp.name.toLowerCase().includes('date') || inp.id.toLowerCase().includes('date')) {{
                                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                                nativeSetter.call(inp, target);
                                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                            }}
                        }}
                    }}''', target_date)

                    # Click Search
                    await page.evaluate('''() => {
                        const buttons = document.querySelectorAll('button, input[type="submit"], input[type="button"]');
                        for (const el of buttons) {
                            const text = (el.textContent || el.value || '').trim();
                            if (text === 'Search') { el.click(); return; }
                        }
                    }''')
                    await page.wait_for_timeout(3000)

                    await page.screenshot(path='booking_page_5.png')
                    logger.info("Screenshot saved: booking_page_5.png")

                    # Re-click the available tee time (now logged in, should add to cart)
                    cart_btn2 = await page.query_selector('a.cart-button:not(.error)') or await page.query_selector('a.cart-button.success')
                    if cart_btn2:
                        await cart_btn2.click()
                        logger.info("Re-clicked available cart button after login")
                        await page.wait_for_timeout(3000)
                    else:
                        logger.warning("Could not find available tee time after re-search")

                    await page.screenshot(path='booking_page_6.png')
                    logger.info("Screenshot saved: booking_page_6.png")

                # Handle "Tee Time Member Selection" - click Continue (appears whether or not login was needed)
                continue_member_btn = await page.query_selector('button:has-text("Continue"), input[value="Continue"]')
                if continue_member_btn:
                    logger.info("Member selection page detected, clicking Continue...")
                    await continue_member_btn.click()
                    await page.wait_for_timeout(3000)
                    await page.screenshot(path='booking_page_7.png')
                    logger.info("Screenshot saved: booking_page_7.png")

                await page.screenshot(path='booking_page_4.png')

                # --- Step 4: Handle credit card / final confirmation page ---
                # The final page shows credit card info and a "Continue" button to confirm
                if self.config['automation']['auto_submit']:
                    final_continue = await page.query_selector(
                        'button:has-text("Continue"), input[value="Continue"], '
                        'button:has-text("Book"), button:has-text("Submit"), '
                        'button:has-text("Checkout"), input[value*="Book"]'
                    )
                    if final_continue:
                        await final_continue.click()
                        logger.info("BOOKING CONFIRMED! Clicked final Continue.")
                        await page.wait_for_timeout(5000)
                        await page.screenshot(path='booking_confirmation.png')
                        logger.info("Screenshot saved: booking_confirmation.png")
                    else:
                        logger.warning("Could not find final confirm button")
                else:
                    logger.info("Auto-submit is disabled. Please manually complete the booking.")
                    logger.info("Browser will remain open for 120 seconds...")
                    await page.wait_for_timeout(120000)

                return True

            except Exception as e:
                logger.error(f"Error during booking: {str(e)}")
                await page.screenshot(path='booking_error.png')
                raise

            finally:
                await browser.close()
    
    async def search_tee_times(self, target_date, num_players=1):
        """
        Search for all available tee times on a given date.

        Args:
            target_date: Date to search (MM/DD/YYYY format)
            num_players: Number of players

        Returns:
            List of dicts with keys: time, holes, course, players
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.config['automation']['headless']
            )
            context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
            page = await context.new_page()

            try:
                await page.goto(self.booking_url, wait_until='networkidle')
                await page.wait_for_timeout(2000)

                # Login first so cart buttons show as available
                await self.login(page)

                # Bail out early if the date is outside the bookable window
                min_date, max_date = await self.get_booking_window(page)
                if min_date and max_date and not self._in_window(target_date, min_date, max_date):
                    logger.info(
                        f"{target_date} is outside the booking window "
                        f"({min_date} - {max_date}), skipping")
                    return []

                await self.set_search_criteria(page, target_date, num_players)
                await self.click_search(page)

                await page.screenshot(path='search_results.png')

                results = await self.scrape_results(page)

                logger.info(f"Found {len(results)} available tee times for {target_date}")
                for r in results:
                    logger.info(f"  {r['time']} - {r['spots']} open")
                return results

            except Exception as e:
                logger.error(f"Error searching tee times: {str(e)}")
                return []

            finally:
                await browser.close()

    async def search_multiple_dates(self, dates, num_players=1):
        """
        Search for available tee times across multiple dates in one browser session.
        Logs in once, then re-uses the session for each date.

        Args:
            dates: List of date strings (MM/DD/YYYY format)
            num_players: Number of players

        Returns:
            Dict mapping date -> list of {time, spots} dicts
        """
        all_results = {}
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.config['automation']['headless']
            )
            context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
            page = await context.new_page()

            try:
                await page.goto(self.booking_url, wait_until='networkidle')
                await page.wait_for_timeout(2000)

                # Login once
                await self.login(page)

                min_date, max_date = await self.get_booking_window(page)
                if min_date and max_date:
                    logger.info(f"Site booking window: {min_date} - {max_date}")

                for target_date in dates:
                    try:
                        if min_date and max_date and not self._in_window(target_date, min_date, max_date):
                            logger.info(f"{target_date} is outside the booking window, skipping")
                            all_results[target_date] = []
                            continue

                        await self.set_search_criteria(page, target_date, num_players)
                        await self.click_search(page)

                        results = await self.scrape_results(page)

                        logger.info(f"Found {len(results)} tee times for {target_date}")
                        all_results[target_date] = results

                        # Navigate back to search form for next date
                        await page.goto(self.booking_url, wait_until='networkidle')
                        await page.wait_for_timeout(1000)

                    except Exception as e:
                        logger.error(f"Error searching {target_date}: {e}")
                        all_results[target_date] = []

            except Exception as e:
                logger.error(f"Error in multi-date search: {e}")

            finally:
                await browser.close()

        return all_results

    async def monitor_and_book(self):
        """
        Open one browser, search for tee times, and book when available.
        Retries by re-searching on the same page instead of opening new browsers.
        """
        preferences = self.config['preferences']
        check_interval = self.config['automation']['check_interval_minutes']

        target_date = (datetime.now() + timedelta(days=preferences['days_ahead'])).strftime('%m/%d/%Y')

        logger.info("Charleston Municipal Tee Time Auto-Booker Started")
        logger.info(f"Target date: {target_date}")
        logger.info(f"Preferred times: {', '.join(preferences['preferred_times'])}")
        logger.info(f"Check interval: {check_interval} minutes")

        # Use the first preferred time as the begin time filter
        target_time = preferences['preferred_times'][0]

        try:
            await self.book_tee_time(target_date, target_time)
            logger.info(f"Successfully booked for {target_date}!")
        except Exception as e:
            logger.error(f"Booking failed: {str(e)}")


async def main():
    """Main entry point"""
    booker = TeeTimeBooker()

    if booker.config['automation'].get('use_imessage', False):
        from imessage_booker import prompt_for_booking, send_booking_result, send_imessage
        phone = os.environ.get('BOOKING_PHONE', booker.config['user_info'].get('phone', ''))

        booking = await prompt_for_booking(phone)
        if not booking:
            logger.error("No booking details received via iMessage, exiting.")
            return

        target_date = booking['date']

        if booking.get('search_only'):
            # Search mode: return all available tee times
            logger.info(f"Searching available tee times for {target_date}...")
            results = await booker.search_tee_times(target_date, booking['players'])
            if results:
                lines = [f"Available for {target_date}:"]
                for r in results:
                    lines.append(f"  {r['time']} - {r['spots']} open")
                send_imessage(phone, '\n'.join(lines))
            else:
                send_imessage(phone, f"No tee times available for {target_date}.")
        else:
            # Booking mode
            booker.config['preferences']['num_players'] = booking['players']
            target_time = booking['time']

            logger.info(f"iMessage booking: {target_date} at {target_time} for {booking['players']} player(s)")
            try:
                booked = await booker.book_tee_time(target_date, target_time)
                if booked:
                    logger.info(f"Successfully booked for {target_date}!")
                    send_booking_result(phone, True, target_date, target_time)
                else:
                    logger.info(f"No availability for {target_date} at {target_time}")
                    send_booking_result(phone, False, target_date, target_time, no_availability=True)
            except Exception as e:
                logger.error(f"Booking failed: {str(e)}")
                send_booking_result(phone, False, target_date, target_time)
    else:
        # Option 1: Book immediately for specific date/time
        # await booker.book_tee_time('2026-02-14', '08:00')

        # Option 2: Continuously monitor and auto-book
        await booker.monitor_and_book()


if __name__ == "__main__":
    asyncio.run(main())

