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
import sys
from datetime import date

from garminconnect import Garmin

from garmin_api import init_api, safe_api_call

# Suppress garminconnect library logging to avoid tracebacks in normal operation
logging.getLogger("garminconnect").setLevel(logging.CRITICAL)


def display_user_info(api: Garmin):
    """Display basic user information with proper error handling."""
    # Get user's full name
    success, full_name, error_msg = safe_api_call(api.get_full_name)
    if success:
        print(f"Hello, {full_name}!")
    else:
        print(f"Could not get full name: {error_msg}")

    # Get user profile number from device info
    success, device_info, error_msg = safe_api_call(api.get_device_last_used)
    if success and device_info and device_info.get("userProfileNumber"):
        print(f"User profile number: {device_info.get('userProfileNumber')}")
    elif not success:
        print(f"Could not get device info: {error_msg}")
    else:
        print("No device info available.")


def display_daily_stats(api: Garmin):
    """Display today's activity statistics with proper error handling."""
    today = date.today().isoformat()

    # Get user summary (steps, calories, etc.)
    success, summary, error_msg = safe_api_call(api.get_user_summary, today)
    if success and summary:
        steps = summary.get("totalSteps", 0)
        distance_km = summary.get("totalDistanceMeters", 0) / 1000
        calories = summary.get("totalKilocalories", 0)
        floors = summary.get("floorsClimbed", 0)

        print(f"\nToday's stats ({today}):")
        print(f"  Steps:    {steps}")
        print(f"  Distance: {distance_km:.2f} km")
        print(f"  Calories: {calories}")
        print(f"  Floors:   {floors}")

        if steps < 5000 or steps > 15000:
            print("  Step count outside typical range.")
        else:
            print("  Step count in healthy range.")
    elif not success:
        print(f"Could not get user summary: {error_msg}")
    else:
        print("No user summary available.")

    # Get hydration data
    success, hydration, error_msg = safe_api_call(api.get_hydration_data, today)
    if success and hydration and hydration.get("valueInML"):
        hydration_ml = int(hydration.get("valueInML", 0))
        hydration_goal = hydration.get("goalInML", 0)
        cups = round(hydration_ml / 240, 1)

        print(f"\nHydration: {hydration_ml} ml ({cups} cups)")
        if hydration_goal > 0:
            pct = round((hydration_ml / hydration_goal) * 100)
            print(f"  Goal: {hydration_goal} ml ({pct}%)")
    elif not success:
        print(f"Could not get hydration: {error_msg}")
    else:
        print("No hydration data available.")


def main():
    """Main example demonstrating basic Garmin Connect API usage."""
    api = init_api()

    if not api:
        print("Failed to authenticate with Garmin Connect.")
        sys.exit(1)

    display_user_info(api)
    display_daily_stats(api)


if __name__ == "__main__":
    main()
