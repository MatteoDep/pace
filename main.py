"""Simple Garmin Connect API Example.
=====================================

This example demonstrates the basic usage of python-garminconnect:
- Authentication with email/password
- Token storage and automatic reuse
- MFA (Multi-Factor Authentication) support
- Comprehensive error handling for all API calls
- Basic API calls for user stats

For a comprehensive demo of all available API calls, see demo.py

Dependencies:
pip3 install garth requests

Environment Variables (optional):
export EMAIL=<your garmin email address>
export PASSWORD=<your garmin password>
export GARMINTOKENS=<path to token storage>
"""

import logging
from datetime import date

from garminconnect import Garmin

from garmin_api import init_api, safe_api_call

# Suppress garminconnect library logging to avoid tracebacks in normal operation
logging.getLogger("garminconnect").setLevel(logging.CRITICAL)


def display_user_info(api: Garmin):
    """Display basic user information with proper error handling."""
    # Get user's full name
    success, _full_name, _error_msg = safe_api_call(api.get_full_name)
    if success:
        pass
    else:
        pass

    # Get user profile number from device info
    success, device_info, _error_msg = safe_api_call(api.get_device_last_used)
    if success and device_info and device_info.get("userProfileNumber"):
        device_info.get("userProfileNumber")
    elif not success:
        pass
    else:
        pass


def display_daily_stats(api: Garmin):
    """Display today's activity statistics with proper error handling."""
    today = date.today().isoformat()

    # Get user summary (steps, calories, etc.)
    success, summary, _error_msg = safe_api_call(api.get_user_summary, today)
    if success and summary:
        steps = summary.get("totalSteps", 0)
        summary.get("totalDistanceMeters", 0) / 1000  # Convert to km
        summary.get("totalKilocalories", 0)
        summary.get("floorsClimbed", 0)

        # Fun motivation based on steps
        if steps < 5000 or steps > 15000:
            pass
        else:
            pass
    elif not success:
        pass
    else:
        pass

    # Get hydration data
    success, hydration, _error_msg = safe_api_call(api.get_hydration_data, today)
    if success and hydration and hydration.get("valueInML"):
        hydration_ml = int(hydration.get("valueInML", 0))
        hydration_goal = hydration.get("goalInML", 0)
        round(hydration_ml / 240, 1)  # 240ml = 1 cup

        if hydration_goal > 0:
            round((hydration_ml / hydration_goal) * 100)
    elif not success:
        pass
    else:
        pass


def main():
    """Main example demonstrating basic Garmin Connect API usage."""
    api = init_api()

    if not api:
        return

    display_user_info(api)
    display_daily_stats(api)


if __name__ == "__main__":
    main()
