#
# schedule.py - Functions to handle scheduling of downloads and reboots
#
import json
import datetime
import os
from syslog import syslog

HOURS_PER_DAY = 24
DAYS_PER_WEEK = 7

#
# check_schedule() - Check that the given schedule is valid
#
def check_schedule(schedule_list):
    day_count = 0
    try:
        for d in schedule_list:
            day = list(d.keys())[0]
            if day != "*":
                if int(day) not in range(0, DAYS_PER_WEEK):
                    return False
            hours = list(d.values())[0]
            hours_list = hours.split("-")
            hour_low = int(hours_list[0])
            if len(hours_list) > 1:
                hour_high = int(hours_list[1])
            else:
                hour_high = hour_low
            if hour_low not in range(0, HOURS_PER_DAY):
                return False
            if hour_high not in range(0, HOURS_PER_DAY):
                return False
            if hour_low > hour_high:
                return False
            day_count = day_count + 1
        return day_count > 0
    except Exception:
        # Any failure to parse is an invalid configuration
        return False


#
# load_schedule() - Load a schedule
#
def load_schedule(cfg_path, schedule_name):
    filepath = "{}/{}.conf".format(cfg_path, schedule_name)
    try:
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                cfg = json.load(f)
            if check_schedule(cfg.get(schedule_name)):
                return cfg[schedule_name]
    except Exception as e:
        syslog("Failed to load schedule from {}: {}".format(filepath, e))
    return None


#
# save_schedule() - Save a schedule
#
def save_schedule(cfg_path, schedule_name, schedule_list):
    if not os.path.exists(cfg_path):
        os.makedirs(cfg_path)
    filepath = "{}/{}.conf".format(cfg_path, schedule_name)
    cfg = {schedule_name: schedule_list}
    with open(filepath, "w+") as f:
        json.dump(cfg, f, sort_keys=True, indent=2, separators=(",", ": "))


#
# next_schedule_window() - Find the start and end of the next
#     available window from the given schedule.  Returns a tuple of the
#     time delta from the date_from (seconds) for the start and end of the next
#     window.  A start delta of 0 means the requested time is in a
#     window; an end delta of 0 (along with start of 0) means no window
#     exists (always on).
#
def next_schedule_window(date_from, schedule_list):
    try:
        # Create a list of all available hours for the week
        schedule_hours = [0 for i in range(0, HOURS_PER_DAY * DAYS_PER_WEEK)]
        for d in schedule_list:
            day = list(d.keys())[0]
            hours = list(d.values())[0]
            hours_list = hours.split("-")
            hour_low = int(hours_list[0])
            if len(hours_list) > 1:
                hour_high = int(hours_list[1])
            else:
                hour_high = hour_low
            if day == "*":
                # Default hours for all days
                for i in range(0, DAYS_PER_WEEK):
                    day_offset = i * HOURS_PER_DAY
                    schedule_hours[
                        day_offset + hour_low : day_offset + hour_high + 1
                    ] = [1] * (hour_high - hour_low + 1)
            else:
                day_offset = int(day) * HOURS_PER_DAY
                schedule_hours[day_offset + hour_low : day_offset + hour_high + 1] = [
                    1
                ] * (hour_high - hour_low + 1)

        delta_start = None
        delta_end = None
        # Compute the starting point and check if it's in a window
        start_hour = (date_from.weekday() * HOURS_PER_DAY) + date_from.hour
        if schedule_hours[start_hour] > 0:
            delta_start = 0

        # Walk the hours starting from the 'from' hour
        for w in range(0, HOURS_PER_DAY * DAYS_PER_WEEK):
            is_window = (
                schedule_hours[(w + start_hour) % (HOURS_PER_DAY * DAYS_PER_WEEK)] > 0
            )
            if delta_start is None:
                # Look for start
                if is_window:
                    date_start = (date_from + datetime.timedelta(hours=w)).replace(
                        minute=0
                    )
                    delta_start = (date_start - date_from).total_seconds()
            elif delta_end is None:
                # Look for end
                if not is_window:
                    date_end = (date_from + datetime.timedelta(hours=w)).replace(
                        minute=0
                    )
                    delta_end = (date_end - date_from).total_seconds()
        if delta_end is None:
            if delta_start is None or delta_start == 0:
                # No windows defined, or entire schedule is available
                delta_start = 0
                delta_end = 0
            else:
                # The window ends at the last interval
                delta_end = HOURS_PER_DAY * DAYS_PER_WEEK
        return (delta_start, delta_end)
    except Exception:
        # Invalid input config, return 'always on'
        return (0, 0)
