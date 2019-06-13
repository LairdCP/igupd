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
import pylibconfig
import traceback

NM_IFACE = 'org.freedesktop.NetworkManager'
NM_OBJ = '/org/freedesktop/NetworkManager'
NM_DEVICE_IFACE = 'org.freedesktop.NetworkManager.Device'
NM_WIFI_DEVICE_IFACE = 'org.freedesktop.NetworkManager.Device.Wireless'
DBUS_PROP_IFACE = 'org.freedesktop.DBus.Properties'

DEVICE_SERVICE_INTERFACE = "com.lairdtech.device.DeviceService"
DEVICE_SERVICE_OBJ_PATH = "/com/lairdtech/device/DeviceService"
PUBLIC_API_INTERFACE = "com.lairdtech.device.public.DeviceInterface"

UPGRADE_AVAILABLE = 'upgrade_available'
UPGRADE_DOWNLOADED = 'upgrade_downloaded'
BOOTSIDE = 'bootside'
ALTBOOTCMD = 'altbootcmd'
BOOTCOUNT = 'bootcount'
BOOTLIMIT = 'bootlimit'

PRIORITY_UPDATE_SCHEDULE = 'priority_update_schedule'
UPDATE_SCHEDULE = 'update_schedule'

ID_CFG_KEY = 'secupdate.id'
WRITE_CFG_KEY = 'secupdate.write_cfg_path'
UPDATE_SCHEDULE_CFG_KEY = 'secupdate.update_schedule'
DAY_CFG_KEY = '.day'
HOURS_CFG_KEY = '.hours'

SWUPDATE = 'swupdate'
IMAGE = 'image'
DEVICE_LED_FAILED = "failed"
DEVICE_LED_RESET = "reset"

SW_CONF_FILE_PATH = '/etc/secupdate.cfg'
SW_VERSION_FILE_PATH = '/var/sw-versions'

kernel_side = {'a': '/dev/ubi0_0', 'b': '/dev/ubi0_3'}
rootfs_side = {'a': '/dev/ubi0_1', 'b': '/dev/ubi0_4'}

components_dict = {'kernel': kernel_side,
                   'rootfs': rootfs_side}

NO_UPDATE_AVAILABLE = 0
UPDATES_AVAILABLE = 1
UPDATES_IN_PROGRESS = 2
CHECK_ABORTED = -1

UPDATE_FAILED = -1
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
        self.config = {}
        self.swupdate_client = None
        self.reboot_start_time = 0
        self.reboot_timer = None
        self.snooze_duration = 0
        self.device_name = None
        self.total_snooze_seconds = 0
        self.usb_local_update = False
        self.switch_side = False
        self.data_migrate_success = True
        self.device_svc = None
        self.updated_component = set()
        self.gen_sw_version()
        self.get_wlan_hw_address()
        self.conn_device_service()
        self.local_update = LocalUpdate(self.process_config, self.start_swupdate, self.device_svc)
        self.update_state = UPDATE_READY
        self.device_name_prefix = 'Laird_'
        self.write_cfg_path = '/data/public/igupd/update_schedule.conf'
        self.public_key_file = None
        self.process_config()

        if get_uboot_env_value(UPGRADE_AVAILABLE) == '1':
            self.verify_startup()
        else:
            self.start_swupdate(False)
        boot_successful(self.public_key_file)

    def get_wlan_hw_address(self):
        bus = dbus.SystemBus()
        nm = dbus.Interface(bus.get_object(NM_IFACE, NM_OBJ), NM_IFACE)
        wifi_dev_obj = bus.get_object(NM_IFACE, nm.GetDeviceByIpIface("wlan0"))
        wifi_dev_props = dbus.Interface(wifi_dev_obj, DBUS_PROP_IFACE)
        self.mac_addr = str(wifi_dev_props.Get(NM_WIFI_DEVICE_IFACE, 'HwAddress'))
        syslog("igupd: get_wlan_hw_address : %s" % self.mac_addr)

    def gen_sw_version(self):
        """
        Creates sw-versions file for swupdate to check with if-different then install
        """
        if os.path.isfile(SW_VERSION_FILE_PATH):
            return

        try:
            md5sum_val = None
            with open(SW_VERSION_FILE_PATH, 'w') as f:
                for key, value in components_dict.items():
                    if key == "kernel":
                        md5sum_val = generate_md5sum(value[self.current_boot_side])
                    elif key == "rootfs":
                        md5sum_val = generate_md5sum(value[self.current_boot_side])
                    else:
                        md5sum_val = generate_md5sum(value)

                    if md5sum_val == -1:
                        syslog("igupd:gen_sw_version: Failed for %s  %s" % (key, md5sum_val))
                    syslog("igupd:gen_sw_version: Writing %s  %s" % (key, md5sum_val))
                    f.write('{}  {}\n'.format(key, md5sum_val))
        except KeyError:
            syslog("igupd: find boot side")
            return 

    def conn_device_service(self):
        """
        Connects to device service API to indicate update status on led
        """
        try:
            bus = dbus.SystemBus()
            proxy = bus.get_object(DEVICE_SERVICE_INTERFACE, DEVICE_SERVICE_OBJ_PATH)
            self.device_svc = dbus.Interface(proxy, PUBLIC_API_INTERFACE)

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
        if PRIORITY_UPDATE_SCHEDULE in self.config:
            self.schedule_reboot(self.config[PRIORITY_UPDATE_SCHEDULE])
        else:
            self.schedule_reboot(self.config[UPDATE_SCHEDULE])
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
            if self.usb_local_update is True and self.device_svc:
                self.device_svc.DeviceUpdating()

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
                #case when update is skipped
                self.update_state = NO_UPDATE_AVAILABLE
                self.updated_component.clear()
                if self.usb_local_update is True:
                    self.local_update_state_change(DEVICE_LED_RESET)
                else:
                    self.start_swupdate(True, SWUPDATE_SUCCESS)

        elif status == swuclient.SWU_STATUS_FAILURE:
            self.update_state = NO_UPDATE_AVAILABLE
            self.updated_component.clear()
            #if swupdate failed in SURICATTA then no need
            #reset config and restart swupdate
            if self.usb_local_update is True:
                self.local_update_state_change(DEVICE_LED_FAILED)

        elif status == swuclient.SWU_STATUS_BAD_CMD:
            self.updated_component.clear()
            if self.usb_local_update is True:
                self.local_update_state_change(DEVICE_LED_FAILED)

    def process_config(self, config=None):
        '''
        If a config is passed, with update_schedule information, then update
        runtime self.config and write to a persistant memory in /data

        if a config is passed, this can also be a complete config, only for local
        update seperately

        If a config is not passed, read the configuration from /rodata/
        and override update_schedule if the file exists in /data/
        '''

        if config is None:
            try:
                c = pylibconfig.Config()
                c.readFile(SW_CONF_FILE_PATH)
                if c.exists(ID_CFG_KEY):
                    self.device_name_prefix, is_valid = c.value(ID_CFG_KEY)
                self.device_name = self.device_name_prefix + self.mac_addr
                syslog('Secure update device ID: ' + self.device_name)
                if c.exists(WRITE_CFG_KEY):
                    self.write_cfg_path, is_valid = c.value(WRITE_CFG_KEY)
                syslog('Secure update config write path: ' + self.write_cfg_path)
                if c.exists('globals.public-key-file'):
                    self.public_key_file, is_valid = c.value('globals.public-key-file')
                # Convert update_schedule from cfg format to dict
                update_schedule = []
                i = 0
                key = UPDATE_SCHEDULE_CFG_KEY + '.[{}]'.format(i)
                while c.exists(key):
                    day_str, day_valid = c.value(key + DAY_CFG_KEY)
                    hours_str, hours_valid = c.value(key + HOURS_CFG_KEY)
                    if day_valid and hours_valid:
                        update_schedule.append({ day_str : hours_str })
                        i = i + 1
                        key = UPDATE_SCHEDULE_CFG_KEY + '.[{}]'.format(i)
                    else:
                        break
                self.config[UPDATE_SCHEDULE] = update_schedule

                #override update_schedule if any exists
                if os.path.exists(self.write_cfg_path):
                    with open(self.write_cfg_path, 'r') as f:
                        temp_config = json.load(f)

                    if UPDATE_SCHEDULE in temp_config:
                        self.config[UPDATE_SCHEDULE] = temp_config[UPDATE_SCHEDULE]

                syslog('Update schedule: {}'.format(self.config[UPDATE_SCHEDULE]))


            except (RuntimeError, IOError):
                syslog('Failed to parse secure update configuration file: {}'.format(traceback.format_exc()))
                return False
        else:
            # if local update
            if IMAGE in config:
                self.config = config
            #if no local update but schedule info in config
            elif self.config is not None:
                if UPDATE_SCHEDULE in config:
                    self.config[UPDATE_SCHEDULE] = config[UPDATE_SCHEDULE]
                    if not os.path.exists(self.write_cfg_path):
                        if not os.path.exists(os.path.dirname(self.write_cfg_path)):
                            os.makedirs(os.path.dirname(self.write_cfg_path))

                    with open(self.write_cfg_path, 'w+') as f:
                            json.dump(config, f, sort_keys=True, indent=2, separators=(',', ': '))
                    syslog("igupd: process_config: update schedule modified successfully")
        return True

    def start_swupdate(self, reply=False, result='1'):
        '''
        Determine the correct boot side for swupdate to copy a new update to and start Swupdate.
        '''

        # Check the current boot side so we can make the appropriate switch later
        if self.current_boot_side == 'a':
            syslog("Current boot_side is a")
            select = 'stable,main-b'
        else:
            syslog("Current boot_side is b")
            select = 'stable,main-a'


        # Check we are using swupdate's suricatta mode or updating locally on the device.
        # If local, don't save the config
        if reply:
            cmd = [SWUPDATE, "-f", SW_CONF_FILE_PATH, "-e", select, "-u", ' -i '+  self.device_name + ' -c ' + result]
            syslog("CONFIG: REPLYING TO HAWKBIT")
        elif IMAGE in self.config:
            syslog("CONFIG: LOCAL IMAGE")
            self.usb_local_update = True
            cmd = [SWUPDATE, "-f", SW_CONF_FILE_PATH, "-e", select, "-i", self.config[IMAGE]]
        else:
            syslog("CONFIG: SURICATTA MODE")
            cmd = [SWUPDATE, "-f", SW_CONF_FILE_PATH, "-e", select, "-u", ' -i '+  self.device_name]

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
            try:
                all_days = False
                v = list(d.values())
                k = list(d.keys())
                hour_low = int(v[0].split('-')[0])
                hour_high = int(v[0].split('-')[1])
                # '*' is any day
                if k[0] == '*':
                    all_days = True
                    day_target = day
                else:
                    day_target = int(k[0])

                # Find the correct day
                if day < day_target: # Days are 0-6
                    days = day_target - day
                elif day > day_target: # Day is behind us
                    days = 7 - (day - day_target)
                else:
                    days = 0 # Schedule reboot today

                # Find the correct hour
                if hour < hour_low: #schedule window is not missed
                    hours = hour_low - hour
                elif hour > hour_high: #schedule window is missed
                    if days == 0 and all_days: #if day is "*" and window missed
                        hours = (24 - hour) + hour_low
                        all_days = False
                    elif days == 0 and (not all_days): #same day window missed
                        days = 7
                        hours = (hour_low - hour)
                    else:
                        hours = (hour_low - hour)
                else:
                    hours = 0 #Schedule reboot now

                run_at = now + datetime.timedelta(hours=hours,days=days)
                syslog(" day_target: {} , hour_low: {}, hour_high: {}, run at : {}".format(day_target, hour_low, hour_high,run_at))
                delay = (run_at - now).total_seconds()
                schedule.append(delay)
            except Exception as e:
                syslog("Exception in update schedule: {}".format(e))
                del schedule[:]
                break
        '''
        Start the reboot timer.  The snooze command will use this timer
        to snooze the reboot
        '''
        if not schedule:
            schedule.append(0.0)
            syslog("Rebooting now...")
        else:
            syslog("list is {}".format(schedule))
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

        if self.switch_side:
            self.data_migrate_success = data_migration()
            if self.data_migrate_success:
                if self.current_boot_side == 'a':
                    set_env(BOOTSIDE, 'b')
                    set_env(ALTBOOTCMD, 'setenv bootside a; saveenv; run bootcmd')
                else:
                    set_env(BOOTSIDE, 'a')
                    set_env(ALTBOOTCMD, 'setenv bootside b; saveenv; run bootcmd')


        if self.data_migrate_success:
            self.UpdatePending(UPDATE_REBOOT)
            set_env(UPGRADE_AVAILABLE, '1')
            set_env(BOOTLIMIT, '5')
            reboot()
        else:
            self.data_migrate_success = True
            self.switch_side = False
            self.update_state = NO_UPDATE_AVAILABLE
            self.UpdatePending(UPDATE_FAILED)
            self.updated_component.clear()
            self.reboot_timer = None
            if self.usb_local_update is True:
                self.local_update_state_change(DEVICE_LED_FAILED)
            else:
                self.start_swupdate(True, SWUPDATE_FAILED)

    def local_update_state_change(self, handler):
        if handler == DEVICE_LED_RESET and self.device_svc:
            self.device_svc.DeviceUpdateReset()
        elif handler == DEVICE_LED_FAILED and self.device_svc:
            self.device_svc.DeviceUpdateFailed()
        self.usb_local_update = False
        self.process_config()
        self.start_swupdate(False)
