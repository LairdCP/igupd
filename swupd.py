import os
import json
import sys
import socket
import time
import random
import thread
import datetime
from syslog import syslog, openlog
from upsvc import UpdateService
from somutil import *
import resumetimer

BLACKLIST="0 1 2 3"
UPGRADE_AVAILABLE='upgrade_available'
UPGRADE_DOWNLOADED='upgrade_downloaded'
BOOTSIDE = 'bootside'

SWUPDATE = 'swupdate'
BLACKLIST = 'blacklist'
SURICATTA = 'suricatta'
DOWNLOAD = 'download'
IMAGE = 'image'
URL = 'url'
TENANT = 'tenant'
SELECT = 'select'

NO_UPDATE_AVAILABLE =    0
UPDATES_AVAILABLE =      1
UPDATES_IN_PROGRESS =     2
CHECK_ABORTED =          -1

UPDATE_SNOOZED =        0
UPDATE_DOWNLOADING =    1
UPDATE_SCHEDULED =      2
UPDATE_REBOOT =         3
UPDATE_READY =          4

MAX_SNOOZE_SECONDS =    7200


class SoftwareUpdate(UpdateService):
    def __init__(self, bus_name):
        super(SoftwareUpdate, self).__init__(bus_name)
        syslog("Starting secure software update")
        self.config = None
        self.swupdate = None

        if get_uboot_env_value(UPGRADE_AVAILABLE) == '1':
            set_env(UPGRADE_AVAILABLE,'0')
            set_env(BOOTCOUNT,0)

        self.current_boot_side = get_uboot_env_value(BOOTSIDE)
        self.reboot_start_time = 0
        self.reboot_timer = None
        self.snooze_duration = 0
        self.total_snooze_seconds = 0
        self.update_state = UPDATE_READY

    def update_available(self):
        '''
        Reset the update_available uboot var to '1'.  Set the 'bootcmd' and
        'bootargs' vars to the new setting. Schedule reboot.
        '''
        set_env(UPGRADE_AVAILABLE,'1')
        if 'priority_update_schedule' in self.config:
            self.schedule_reboot(self.config['priority_update_schedule'])
        else:
            self.schedule_reboot(self.config['update_schedule'])
        self.UpdatePending(UPDATE_SCHEDULED)

    def attempt_update(self):
        '''
        Check for and update and return True no matter what so
        the gobject main loop will continue to check for updates
        using the random interval
        '''
        self.check_for_update(True)
        return True

    def check_sw_update(self):
        '''
        Poll swupdate to find out if it executed succesfully and there is
        update
        '''
        if self.swupdate == None:
            pass
        elif self.swupdate.poll() == 0:
            self.swupdate = None
            self.update_available()
        elif self.swupdate.poll() == None:
            pass
        else:
            self.swupdate = None

        return True

    def check_for_update(self,perform_update):
        '''
        Run swupdate to check for an update. Later we will poll for
        return value
        '''
        if self.swupdate:
                return UPDATES_IN_PROGRESS

        if self.config == None or self.reboot_start_time > 0:
            return CHECK_ABORTED

        if self.current_boot_side == 'a':
            syslog("Current boot_side is a")
            mode = self.config[SELECT] + '-b'
        else:
            syslog("Current boot_side is b")
            mode = self.config[SELECT] + '-a'

        if perform_update == True:
            if SURICATTA in self.config: # Suricatta used for hawkbit instances
                cmd = [SWUPDATE, "-b", self.config[BLACKLIST], "-e", mode, "-l", "5", "-u", "-t " + self.config[SURICATTA][TENANT]+ " -u "+ self.config[SURICATTA][URL]+" -i "+ self.config[SURICATTA][ID]]
            elif DOWNLOAD in self.config: # Download if using a stand alone web server
                cmd = [SWUPDATE, "-b", self.config[BLACKLIST], "-e", mode, "-l", "5", "-d", "-u "+ self.config[DOWNLOAD][URL]]
            elif IMAGE in self.config: # Local image
                cmd = [SWUPDATE, "-b", self.config[BLACKLIST], "-e", mode, "-l", "5", "-i", self.config[IMAGE]]
            else:
                return CHECK_ABORTED

            # Start swupdate
            self.swupdate = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.UpdatePending(UPDATE_DOWNLOADING)
            return UPDATES_IN_PROGRESS
        else:
            return NO_UPDATE_AVAILABLE

    def schedule_reboot(self,update_list):
        '''
        Parse the update schedule from the config.  Determine the next
        update and find the amount of time until the update.  Finally,
        schedule the update
        '''
        now = datetime.datetime.now()
        day = datetime.datetime.today().weekday()
        hour = datetime.datetime.today().hour

        schedule = []
        for d in update_list:
            hour_low = int(d.values()[0].split('-')[0])
            hour_high = int(d.values()[0].split('-')[1])
            # '*' is any day
            if d.keys()[0] == '*':
                day_target = day
            else:
                day_target = int(d.keys()[0])
            # Find the correct day
            if day < day_target: # Days are 0-6
                days = day_target - day
            elif day > day_target: # Day is behind us
                days = 7 - (day - day_target)
            else:
                days = 0 # Schedule reboot today
            # Find the correct hour
            if hour < hour_low:
                hours = hour_low - hour
            elif hour > hour_high:
                hours = hour_low
            else:
                hours = 0 # Schedule reboot now

            run_at = now + datetime.timedelta(hours=hours,days=days)
            delay = (run_at - now).total_seconds()
            schedule.append(delay)

        '''
        Start the reboot timer.  The snooze command will use this timer
        to snooze the reboot
        '''
        syslog("Rebooting in: "+str(min(schedule)))
        self.reboot_timer = ResumableTimer(min(schedule), self.reboot)
        self.reboot_timer.start()
        self.UpdatePending(UPDATE_SCHEDULED)

    def snooze_reboot(self,snooze_seconds):
        # No update if the reboot_timer isn't instantiated
        if self.reboot_timer is None:
            return -1

        ret = self.reboot_timer.pause(snooze_seconds)
        if ret == 0: # Snooze
            self.UpdatePending(UPDATE_SNOOZED)
        return ret

    def reboot(self):
        '''
        Use the IG's reboot command to initiate the reboot
        '''
        self.UpdatePending(UPDATE_REBOOT)
        reboot()
