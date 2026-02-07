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

                # Store credentials for login when prompted later
                password = self.config['user_info'].get('password', '')
                username = self.config['user_info'].get('username', '') or self.config['user_info'].get('email', '')

                await page.screenshot(path='booking_page_1.png')
                logger.info("Screenshot saved: booking_page_1.png")

                # --- Step 1: Set search filters ---

                # Debug: log all selects and inputs on the page
                select_info = await page.evaluate('''() => {
                    const selects = document.querySelectorAll('select');
                    return Array.from(selects).map((s, i) => ({
                        index: i,
                        name: s.name,
                        id: s.id,
                        options: Array.from(s.options).map(o => ({value: o.value, text: o.text.trim()}))
                    }));
                }''')
                for s in select_info:
                    logger.info(f"Found select[{s['index']}] name='{s['name']}' id='{s['id']}' options={s['options'][:6]}")

                input_info = await page.evaluate('''() => {
                    const inputs = document.querySelectorAll('input');
                    return Array.from(inputs).map((inp, i) => ({
                        index: i, type: inp.type, name: inp.name, id: inp.id, value: inp.value
                    }));
                }''')
                for inp in input_info:
                    logger.info(f"Found input[{inp['index']}] type='{inp['type']}' name='{inp['name']}' id='{inp['id']}' value='{inp['value']}'")

                # Number of players - use JavaScript to set value and trigger change
                players = str(self.config['preferences']['num_players'])
                player_set = await page.evaluate(f'''(target) => {{
                    const selects = document.querySelectorAll('select');
                    for (const s of selects) {{
                        const opts = Array.from(s.options).map(o => o.text.trim().toLowerCase());
                        if (opts.some(o => /^[1-4]$/.test(o))) {{
                            for (const o of s.options) {{
                                if (o.text.trim() === target || o.value === target) {{
                                    s.value = o.value;
                                    s.dispatchEvent(new Event('change', {{bubbles: true}}));
                                    return {{found: true, name: s.name, selected: o.text.trim()}};
                                }}
                            }}
                        }}
                    }}
                    return {{found: false}};
                }}''', players)
                if player_set.get('found'):
                    logger.info(f"Selected {player_set['selected']} player(s) via {player_set['name']}")
                else:
                    logger.warning("Could not find/set player count dropdown")

                # Date field - try multiple approaches
                date_set = await page.evaluate(f'''(target) => {{
                    const inputs = document.querySelectorAll('input');
                    for (const inp of inputs) {{
                        if (inp.type === 'date' || inp.name.toLowerCase().includes('date') || inp.id.toLowerCase().includes('date')) {{
                            const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                            nativeSetter.call(inp, target);
                            inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                            inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                            return {{found: true, name: inp.name, id: inp.id}};
                        }}
                    }}
                    return {{found: false}};
                }}''', target_date)
                if date_set.get('found'):
                    logger.info(f"Set date to {target_date} via {date_set.get('name') or date_set.get('id')}")
                else:
                    logger.warning("Could not find date input field")

                # Begin time dropdown
                hour = int(target_time.split(':')[0])
                minute = target_time.split(':')[1]
                period = 'am' if hour < 12 else 'pm'
                display_hour = hour if hour <= 12 else hour - 12
                if display_hour == 0:
                    display_hour = 12
                time_12h = f"{display_hour:02d}:{minute} {period}"

                # Try multiple time formats: "02:30 pm", "2:30 pm", "2:30 PM", "14:30"
                time_variants = [
                    time_12h,
                    f"{display_hour}:{minute} {period}",
                    f"{display_hour:02d}:{minute} {period.upper()}",
                    f"{display_hour}:{minute} {period.upper()}",
                    target_time,
                ]

                time_set = await page.evaluate('''(variants) => {
                    const selects = document.querySelectorAll('select');
                    for (const s of selects) {
                        const opts = Array.from(s.options).map(o => o.text.trim());
                        const optsLower = opts.map(o => o.toLowerCase());
                        if (optsLower.some(o => o.includes('am') || o.includes('pm'))) {
                            for (const target of variants) {
                                for (const o of s.options) {
                                    if (o.text.trim().toLowerCase() === target.toLowerCase()) {
                                        s.value = o.value;
                                        s.dispatchEvent(new Event('change', {bubbles: true}));
                                        return {found: true, name: s.name, selected: o.text.trim()};
                                    }
                                }
                            }
                            return {found: false, available: opts};
                        }
                    }
                    return {found: false};
                }''', time_variants)
                if time_set.get('found'):
                    logger.info(f"Selected begin time: {time_set['selected']}")
                else:
                    logger.warning(f"Could not select time {time_12h}. Available: {time_set.get('available', 'unknown')}")

                await page.screenshot(path='booking_page_2.png')
                await page.wait_for_timeout(500)

                # --- Step 2: Click Search (sidebar button, not nav link) ---
                search_clicked = await page.evaluate('''() => {
                    // Target the sidebar Search button specifically, not the nav menu
                    const buttons = document.querySelectorAll('button, input[type="submit"], input[type="button"]');
                    for (const el of buttons) {
                        const text = (el.textContent || el.value || '').trim();
                        if (text === 'Search') {
                            el.click();
                            return {found: true, text: text, tag: el.tagName};
                        }
                    }
                    return {found: false};
                }''')
                if search_clicked.get('found'):
                    logger.info(f"Clicked '{search_clicked['text']}'")
                    await page.wait_for_timeout(3000)
                else:
                    logger.warning("Could not find Search button")

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

            except Exception as e:
                logger.error(f"Error during booking: {str(e)}")
                await page.screenshot(path='booking_error.png')
                raise

            finally:
                await browser.close()
    
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
    
    # Option 1: Book immediately for specific date/time
    # await booker.book_tee_time('2026-02-14', '08:00')
    
    # Option 2: Continuously monitor and auto-book
    await booker.monitor_and_book()


if __name__ == "__main__":
    asyncio.run(main())

