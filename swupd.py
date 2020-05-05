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
from schedule import *
import re

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

UPDATE_SCHEDULE = 'update_schedule'
DOWNLOAD_SCHEDULE = 'download_schedule'

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
LAIRD_RELEASE_FILE_PATH = '/etc/os-release'

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
        self.sslkey = None
        self.download_start_timer = None
        self.download_end_timer = None
        self.process_config()

        if get_uboot_env_value(UPGRADE_AVAILABLE) == '1':
            self.verify_startup()
        else:
            self.start_swupdate(False)
        boot_successful(self.public_key_file, self.sslkey)

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
            data = None
            words = []
            laird_version = None
            with open(LAIRD_RELEASE_FILE_PATH, 'r') as f2:
                for line in f2:
                    if re.search('^VERSION=', line):
                        words = line.split()
                        if len(words) > 4:
                            laird_version = words[4]
                        else:
                            laird_version = 0
                syslog("igupd:gen_sw_version: laird {}".format(laird_version))
                with open(SW_VERSION_FILE_PATH, 'w') as f1:
                    for key, value in components_dict.items():
                        if key == "kernel" or key == 'rootfs':
                            f1.write('{}  {}\n'.format(key, laird_version))
                        else:
                            syslog("igupd:gen_sw_version: key not matched")
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
        self.schedule_reboot(self.config.get(UPDATE_SCHEDULE))
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
            if self.usb_local_update is True:
                self.local_update_state_change(DEVICE_LED_FAILED)
            else:
                self.start_swupdate()

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
                if c.exists('suricatta.sslkey'):
                    self.sslkey, is_valid = c.value('suricatta.sslkey')
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
                if check_schedule(update_schedule):
                    self.config[UPDATE_SCHEDULE] = update_schedule

                # Override update_schedule if config file exists
                update_schedule = load_schedule(self.write_cfg_path, UPDATE_SCHEDULE)
                if update_schedule:
                    self.config[UPDATE_SCHEDULE] = update_schedule
                    syslog('Update schedule: {}'.format(self.config[UPDATE_SCHEDULE]))

                # Load download_schedule from config file
                download_schedule = load_schedule(self.write_cfg_path, DOWNLOAD_SCHEDULE)
                if download_schedule:
                    self.config[DOWNLOAD_SCHEDULE] = download_schedule
                    syslog('Download schedule: {}'.format(self.config[DOWNLOAD_SCHEDULE]))

            except (RuntimeError, IOError):
                syslog('Failed to parse secure update configuration file: {}'.format(traceback.format_exc()))
                return False
        else:
            # if local update
            if IMAGE in config:
                self.config = config
            #if no local update but schedule info in config
            elif self.config is not None:
                ret = False
                try:
                    if check_schedule(config.get(UPDATE_SCHEDULE)):
                        self.config[UPDATE_SCHEDULE] = config[UPDATE_SCHEDULE]
                        save_schedule(self.write_cfg_path, UPDATE_SCHEDULE, self.config[UPDATE_SCHEDULE])
                        syslog('igupd: process_config: update schedule modified successfully: {}'.format(self.config[UPDATE_SCHEDULE]))
                        ret = True
                    if check_schedule(config.get(DOWNLOAD_SCHEDULE)):
                        self.config[DOWNLOAD_SCHEDULE] = config[DOWNLOAD_SCHEDULE]
                        save_schedule(self.write_cfg_path, DOWNLOAD_SCHEDULE, self.config[DOWNLOAD_SCHEDULE])
                        syslog('igupd: process_config: download schedule modified successfully: {}'.format(self.config[DOWNLOAD_SCHEDULE]))
                        # Restart download window
                        now = datetime.datetime.now()
                        self.schedule_download_window(now)
                        ret = True
                    return ret
                except (TypeError, AttributeError, ValueError):
                    return False
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
            cmd = [SWUPDATE, "-f", SW_CONF_FILE_PATH, "-e", select, "-u", '-i '+  self.device_name + ' -c ' + result]
            syslog("CONFIG: REPLYING TO HAWKBIT")
        elif IMAGE in self.config:
            syslog("CONFIG: LOCAL IMAGE")
            self.usb_local_update = True
            cmd = [SWUPDATE, "-f", SW_CONF_FILE_PATH, "-e", select, "-i", self.config[IMAGE]]
        else:
            syslog("CONFIG: SURICATTA MODE")
            cmd = [SWUPDATE, "-f", SW_CONF_FILE_PATH, "-e", select, "-u", '-i '+  self.device_name]

        # If we've already started the swupdate thread, pass in the new command and
        # and restart swupdate.
        if self.swupdate_client == None:
            self.swupdate_client = swuclient.SWUpdateClient(self.swupdate_handler, cmd)
            self.swupdate_client.start()
        else:
            self.swupdate_client.set_command(cmd)
            self.swupdate_client.restart_swupdate()
        if not self.usb_local_update:
            now = datetime.datetime.now()
            self.schedule_download_window(now)
        return True


    def schedule_reboot(self, update_list):
        '''
        Parse the update schedule to determine the delta of seconds
        until the next update window.
        '''
        now = datetime.datetime.now()
        delta_start, delta_end = next_schedule_window(now, update_list)
        '''
        Start the reboot timer.  The snooze command will use this timer
        to snooze the reboot
        '''
        syslog('Rebooting in {} seconds.'.format(delta_start))
        self.reboot_timer = resumetimer.ResumableTimer(delta_start, self.reboot)
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

    def download_start(self):
        syslog('Starting suricatta download.')
        self.swupdate_client.suricatta_enable(True)

    def download_end(self):
        syslog('Stopping suricatta download.')
        self.swupdate_client.suricatta_enable(False)
        # Schedule next window; add 30 seconds to make sure
        # the current window has ended
        nowish = datetime.datetime.now() + datetime.timedelta(seconds=30)
        self.schedule_download_window(nowish)

    def schedule_download_window(self, date_from):
        # Stop existing timers
        if self.download_start_timer:
            self.download_start_timer.cancel()
            self.download_start_timer = None
        if self.download_end_timer:
            self.download_end_timer.cancel()
            self.download_end_timer = None
        # Determine the window start and stop based on the current time
        delta_start, delta_end = next_schedule_window(date_from, self.config.get(DOWNLOAD_SCHEDULE))
        if delta_end > 0:
            self.swupdate_client.suricatta_enable(False)
            syslog('Scheduling download window from {} to {}.'.format(delta_start, delta_end))
            self.download_start_timer = threading.Timer(delta_start,
                self.download_start)
            self.download_start_timer.start()
            self.download_end_timer = threading.Timer(delta_end,
                self.download_end)
            self.download_end_timer.start()
        else:
            syslog('Enabling suricatta.')
            self.swupdate_client.suricatta_enable(True)
