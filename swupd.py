import os
import json
import random
import datetime
import dbus.service
import dbus.exceptions
from syslog import syslog
from upsvc import UpdateService
from somutil import *
import resumetimer
import swuclient
from usbupd import LocalUpdate

DEVICE_SERVICE_INTERFACE = "com.lairdtech.device.DeviceService"
DEVICE_SERVICE_OBJ_PATH = "/com/lairdtech/device/DeviceService"
PUBLIC_API_INTERFACE = "com.lairdtech.device.public.DeviceInterface"

PUBLIC_KEY_PATH = '/rodata/secret/igupd/dev.crt'

BLACKLIST = '"2 3"'
UPGRADE_AVAILABLE = 'upgrade_available'
UPGRADE_DOWNLOADED = 'upgrade_downloaded'
BOOTSIDE = 'bootside'
ALTBOOTCMD = 'altbootcmd'
BOOTCOUNT = 'bootcount'
BOOTLIMIT = 'bootlimit'

SWUPDATE = 'swupdate'
BLACKLIST = 'blacklist'
SURICATTA = 'suricatta'
DOWNLOAD = 'download'
IMAGE = 'image'
URL = 'url'
TENANT = 'tenant'
SELECT = 'select'
ID = "id"

SW_VERSION_FILE_PATH = '/var/sw-versions'
kernel_side = {'a': '/dev/ubi0_0', 'b': '/dev/ubi0_3'}
rootfs_side = {'a': '/dev/ubi0_1', 'b': '/dev/ubi0_4'}

components_dict = {'kernel': kernel_side,
                   'rootfs': rootfs_side,
                   'rodata': '/dev/ubi0_6'}

NO_UPDATE_AVAILABLE = 0
UPDATES_AVAILABLE = 1
UPDATES_IN_PROGRESS = 2
CHECK_ABORTED = -1

UPDATE_SNOOZED = 0
UPDATE_DOWNLOADING = 1
UPDATE_SCHEDULED = 2
UPDATE_REBOOT = 3
UPDATE_READY = 4
MAX_SNOOZE_SECONDS = 7200
SWUPDATE_SUCCESS = '2'
SWUPDATE_FAILED = '3'


class SoftwareUpdate(UpdateService):
    def __init__(self, bus_name):
        super(SoftwareUpdate, self).__init__(bus_name)
        syslog("Starting secure software update")
        self.current_boot_side = get_uboot_env_value(BOOTSIDE)
        self.config = None
        self.swupdate_client = None
        self.reboot_start_time = 0
        self.reboot_timer = None
        self.snooze_duration = 0
        self.total_snooze_seconds = 0
        self.usb_local_update = False
        self.switch_side = False
        self.manager = None
        self.updated_component = set()
        self.gen_sw_version()
        self.conn_device_service()
        if self.manager is not None:
            self.local_update = LocalUpdate(self.process_config, self.start_swupdate, self.manager)
        self.update_state = UPDATE_READY
        self.process_config()

        if get_uboot_env_value(UPGRADE_AVAILABLE) == '1':
            self.verify_startup()
        else:
            self.start_swupdate(False)

    def gen_sw_version(self):
        """
        Creates sw-versions file for swupdate to check with if-different then install
        """
        if not os.path.isfile(SW_VERSION_FILE_PATH):
            md5sum_val = None
            with open(SW_VERSION_FILE_PATH, 'w') as f:
                for key, value in components_dict.iteritems():
                    if key == "kernel":
                        md5sum_val = generate_md5sum(value[self.current_boot_side])
                    elif key == "rootfs":
                        md5sum_val = generate_md5sum(value[self.current_boot_side])

                    if md5sum_val == -1:
                        syslog("igupd:gen_sw_version: Failed for %s  %s" % (key, md5sum_val))
                    syslog("igupd:gen_sw_version: Writing %s  %s" % (key, md5sum_val))
                    f.write('{}  {}\n'.format(key, md5sum_val))

    def conn_device_service(self):
        """
        Connects to device service API to indicate update status on led
        """
        try:

            bus = dbus.SystemBus()
            proxy = bus.get_object(DEVICE_SERVICE_INTERFACE, DEVICE_SERVICE_OBJ_PATH)
            self.manager = dbus.Interface(proxy, PUBLIC_API_INTERFACE)

        except dbus.exceptions.DBusException as e:
            syslog("swupd: conn_device_service: %s" % e)

    def verify_startup(self):
        '''
        Determine whether or not the startup was successful, if this was
        a fallback, and update Hawkbit accordingly.
        '''
        if int(get_uboot_env_value(BOOTCOUNT)) > 5:
            self.start_swupdate(True, SWUPDATE_FAILED)
        else:
            self.start_swupdate(True, SWUPDATE_SUCCESS)

        set_env(UPGRADE_AVAILABLE, '0')
        set_env(BOOTCOUNT, '0')
        return True

    def update_available(self):
        '''
        Reset the update_available uboot var to '1'.  Set the 'bootcmd' and
        'bootargs' vars to the new setting. Schedule reboot.
        '''
        if 'priority_update_schedule' in self.config:
            self.schedule_reboot(self.config['priority_update_schedule'])
        else:
            self.schedule_reboot(self.config['update_schedule'])
        self.UpdatePending(UPDATE_SCHEDULED)
        self.update_state = UPDATES_AVAILABLE

    def check_update(self, perform_update):
        return self.update_state

    def swupdate_handler(self, status, curr_img, msg):
        '''
        Receive handler for swupdate.  Process the signals and keep
        track of the state.
        '''
        if curr_img:
            self.updated_component.add(curr_img)

        if status == swuclient.SWU_STATUS_START:
            if self.usb_local_update is True:
                self.manager.DeviceUpdating()

            self.UpdatePending(UPDATE_DOWNLOADING)
            self.update_state = UPDATES_IN_PROGRESS

        elif status == swuclient.SWU_STATUS_SUCCESS:
            if self.updated_component:
                if 'kernel.itb' in self.updated_component and 'rootfs.bin' in self.updated_component:
                    for keys in self.updated_component:
                        syslog("swupdate_handler: Components updated are : %s" % keys)
                    self.switch_side = True
                self.update_available()
                self.updated_component.clear()
            else:
                self.update_state = NO_UPDATE_AVAILABLE
                self.updated_component.clear()
                if self.usb_local_update is True:
                    self.manager.DeviceUpdateReset()
                    self.usb_local_update = False
                    self.process_config()
                    self.start_swupdate(False)

        elif status == swuclient.SWU_STATUS_FAILURE:
            self.update_state = NO_UPDATE_AVAILABLE
            self.updated_component.clear()
            #if swupdate failed in SURICATTA then no need
            #reset config and restart swupdate
            if self.usb_local_update is True:
                self.manager.DeviceUpdateFailed()
                self.usb_local_update = False
                self.process_config()
                self.start_swupdate(False)

        elif status == swuclient.SWU_STATUS_BAD_CMD:
            self.updated_component.clear()
            if self.usb_local_update is True:
                self.manager.DeviceUpdateFailed()
                self.usb_local_update = False
                self.process_config()
                self.start_swupdate(False)

    def process_config(self, config=None):
        '''
        If a config is passed, use it or load from persistent storage
        '''
        self.config = config
        if self.config is None:
            try:
                with open(self.SWU_CONFIG_PATH, 'r') as f:
                    self.config = json.load(f)
            except IOError:
                return False
        else:
            if SURICATTA in self.config:
                with open(self.SWU_CONFIG_PATH, 'w') as f:
                    json.dump(self.config, f, sort_keys=True, indent=2, separators=(',', ': '))

        return True

    def start_swupdate(self, reply=False, result='1' ):
        '''
        Determine the correct boot side for swupdate to copy a new update to and start Swupdate.
        '''

        # Check the current boot side so we can make the appropriate switch later
        mode = None
        if self.current_boot_side == 'a':
            syslog("Current boot_side is a")
            mode = self.config[SELECT] + '-b'
        else:
            syslog("Current boot_side is b")
            mode = self.config[SELECT] + '-a'


        # Check we are using swupdate's suricatta mode or updating locally on the device.
        # If local, don't save the config
        if reply:
            cmd = [SWUPDATE, "-b", '"'+self.config[BLACKLIST]+'"', "-e", mode, "-l", "5", "-u",'-u '+ self.config[SURICATTA][URL] + ' -t ' + self.config[SURICATTA][TENANT] + ' -i '+ self.config[SURICATTA][ID] + ' -c ' + result + ' -p ' + str(random.randint(1,30)), "-k", PUBLIC_KEY_PATH]
        elif SURICATTA in self.config:
            syslog("CONFIG: SURICATTA MODE")
            cmd = [SWUPDATE, "-b", '"'+self.config[BLACKLIST]+'"', "-e", mode, "-l", "5", "-u",'-u '+ self.config[SURICATTA][URL] + ' -t ' + self.config[SURICATTA][TENANT] + ' -i '+ self.config[SURICATTA][ID] + ' -p ' + str(random.randint(1,30)), "-k", PUBLIC_KEY_PATH]
        elif IMAGE in self.config:
            syslog("CONFIG: LOCAL IMAGE")
            self.usb_local_update = True
            cmd = [SWUPDATE, "-b", '"'+self.config[BLACKLIST]+'"', "-e", mode, "-l", "5", "-i", self.config[IMAGE], "-k", PUBLIC_KEY_PATH]
        else:
            self.update_state = CHECK_ABORTED
            return False

        # If we've already started the swupdate thread, pass in the new command and
        # and restart swupdate.
        if self.swupdate_client == None:
            self.swupdate_client = swuclient.SWUpdateClient(self.swupdate_handler, cmd)
            self.swupdate_client.start()
        else:
            self.swupdate_client.set_command(cmd)
            self.swupdate_client.restart_swupdate()

        return True


    def schedule_reboot(self, update_list):
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
        self.reboot_timer = resumetimer.ResumableTimer(min(schedule), self.reboot)
        self.reboot_timer.start()
        self.UpdatePending(UPDATE_SCHEDULED)

    def snooze_reboot(self, snooze_seconds):
        '''
        If a reboot is schedule, stall the reboot for the specified time
        '''
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
        set_env(UPGRADE_AVAILABLE, '1')
        set_env(BOOTLIMIT, '5')

        if self.switch_side:
            if self.current_boot_side == 'a':
                set_env(BOOTSIDE, 'b')
                set_env(ALTBOOTCMD, 'setenv bootside a; saveenv; run bootcmd')
            else:
                set_env(BOOTSIDE, 'a')
                set_env(ALTBOOTCMD, 'setenv bootside b; saveenv; run bootcmd')
        reboot()
