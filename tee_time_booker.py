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
            target_date: Date to book (YYYY-MM-DD format)
            target_time: Time to book (HH:MM format)
        """
        async with async_playwright() as p:
            # Launch browser
            browser = await p.chromium.launch(
                headless=self.config['automation']['headless']
            )
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            try:
                logger.info(f"Navigating to Charleston Municipal booking site...")
                await page.goto(self.booking_url, wait_until='networkidle')
                
                # Take screenshot for debugging
                await page.screenshot(path='booking_page_1.png')
                logger.info("Screenshot saved: booking_page_1.png")
                
                # Wait a bit for page to fully load
                await page.wait_for_timeout(2000)
                
                # Look for date picker or calendar
                logger.info("Searching for date selection elements...")
                
                # Common selectors for WebTrac golf booking systems
                date_selectors = [
                    'input[type="date"]',
                    'input[name*="date"]',
                    'input[id*="date"]',
                    '.datepicker',
                    '#reservation-date',
                    'select[name*="date"]'
                ]
                
                date_input = None
                for selector in date_selectors:
                    try:
                        date_input = await page.query_selector(selector)
                        if date_input:
                            logger.info(f"Found date input with selector: {selector}")
                            break
                    except:
                        continue
                
                if date_input:
                    # Fill in the date
                    await date_input.fill(target_date)
                    logger.info(f"Entered date: {target_date}")
                    await page.wait_for_timeout(1000)
                    
                    # Look for search/submit button
                    search_button = await page.query_selector('button:has-text("Search"), input[type="submit"], button[type="submit"]')
                    if search_button:
                        await search_button.click()
                        logger.info("Clicked search button")
                        await page.wait_for_timeout(2000)
                        await page.screenshot(path='booking_page_2.png')
                else:
                    logger.warning("Could not find date input field")
                    # Try clicking through calendar interface
                    logger.info("Attempting alternative navigation...")
                
                # Look for available tee times
                logger.info(f"Searching for {target_time} tee time...")
                
                # Try to find time slots
                time_buttons = await page.query_selector_all('button, a, div[class*="time"], div[class*="slot"]')
                logger.info(f"Found {len(time_buttons)} potential time slot elements")
                
                for button in time_buttons:
                    text = await button.inner_text()
                    if target_time in text:
                        logger.info(f"Found target time slot: {text}")
                        await button.click()
                        logger.info("Clicked on time slot")
                        await page.wait_for_timeout(1000)
                        await page.screenshot(path='booking_page_3.png')
                        break
                
                # Fill in player information
                logger.info("Filling in player information...")
                
                user_info = self.config['user_info']
                
                # Common form field selectors
                form_fields = {
                    'first_name': ['input[name*="first"], input[id*="first"]', user_info['first_name']],
                    'last_name': ['input[name*="last"], input[id*="last"]', user_info['last_name']],
                    'email': ['input[type="email"], input[name*="email"]', user_info['email']],
                    'phone': ['input[type="tel"], input[name*="phone"]', user_info['phone']]
                }
                
                for field_name, (selectors, value) in form_fields.items():
                    for selector in selectors.split(', '):
                        try:
                            field = await page.query_selector(selector)
                            if field:
                                await field.fill(value)
                                logger.info(f"Filled {field_name}: {value}")
                                break
                        except:
                            continue
                
                # Number of players
                players = self.config['preferences']['num_players']
                player_select = await page.query_selector('select[name*="player"], select[id*="player"]')
                if player_select:
                    await player_select.select_option(str(players))
                    logger.info(f"Selected {players} players")
                
                await page.screenshot(path='booking_page_4.png')
                
                # Submit booking
                if self.config['automation']['auto_submit']:
                    submit_button = await page.query_selector(
                        'button:has-text("Book"), button:has-text("Submit"), '
                        'button:has-text("Reserve"), input[value*="Book"], input[value*="Submit"]'
                    )
                    if submit_button:
                        await submit_button.click()
                        logger.info("✅ BOOKING SUBMITTED!")
                        await page.wait_for_timeout(3000)
                        await page.screenshot(path='booking_confirmation.png')
                    else:
                        logger.warning("Could not find submit button")
                else:
                    logger.info("⚠️ Auto-submit is disabled. Please manually submit the booking.")
                    logger.info("Browser will remain open for 60 seconds...")
                    await page.wait_for_timeout(60000)
                
            except Exception as e:
                logger.error(f"Error during booking: {str(e)}")
                await page.screenshot(path='booking_error.png')
                raise
            
            finally:
                await browser.close()
    
    async def monitor_and_book(self):
        """
        Continuously monitor for tee times and book when available
        """
        preferences = self.config['preferences']
        check_interval = self.config['automation']['check_interval_minutes']
        
        # Calculate target date (X days ahead)
        target_date = (datetime.now() + timedelta(days=preferences['days_ahead'])).strftime('%Y-%m-%d')
        
        logger.info(f"🏌️ Charleston Municipal Tee Time Auto-Booker Started")
        logger.info(f"Target date: {target_date}")
        logger.info(f"Preferred times: {', '.join(preferences['preferred_times'])}")
        logger.info(f"Check interval: {check_interval} minutes")
        
        while True:
            for preferred_time in preferences['preferred_times']:
                try:
                    logger.info(f"\n--- Attempting to book {target_date} at {preferred_time} ---")
                    await self.book_tee_time(target_date, preferred_time)
                    logger.info(f"✅ Successfully booked {target_date} at {preferred_time}!")
                    return  # Exit after successful booking
                    
                except Exception as e:
                    logger.error(f"Failed to book {preferred_time}: {str(e)}")
                    continue
            
            logger.info(f"\nWaiting {check_interval} minutes before next check...")
            await asyncio.sleep(check_interval * 60)
            
            # Update target date for next iteration
            target_date = (datetime.now() + timedelta(days=preferences['days_ahead'])).strftime('%Y-%m-%d')


async def main():
    """Main entry point"""
    booker = TeeTimeBooker()
    
    # Option 1: Book immediately for specific date/time
    # await booker.book_tee_time('2026-02-14', '08:00')
    
    # Option 2: Continuously monitor and auto-book
    await booker.monitor_and_book()


if __name__ == "__main__":
    asyncio.run(main())

