import os
import datetime
import dbus
import dbus.service
import dbus.exceptions
from syslog import syslog
import libconf
import traceback

from .upsvc import UpdateService
from .somutil import *
from . import resumetimer
from . import swuclient
from .usbupd import LocalUpdate
from .schedule import *

NM_IFACE = "org.freedesktop.NetworkManager"
NM_OBJ = "/org/freedesktop/NetworkManager"
NM_DEVICE_IFACE = "org.freedesktop.NetworkManager.Device"
NM_WIFI_DEVICE_IFACE = "org.freedesktop.NetworkManager.Device.Wireless"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"

DEVICE_SERVICE_INTERFACE = "com.lairdtech.device.DeviceService"
DEVICE_SERVICE_OBJ_PATH = "/com/lairdtech/device/DeviceService"
PUBLIC_API_INTERFACE = "com.lairdtech.device.public.DeviceInterface"

UPGRADE_AVAILABLE = "upgrade_available"
UPGRADE_DOWNLOADED = "upgrade_downloaded"
BOOTSIDE = "bootside"
ALTBOOTCMD = "altbootcmd"
BOOTCOUNT = "bootcount"
BOOTLIMIT = "bootlimit"

UPDATE_SCHEDULE = "update_schedule"

GLOBALS_CFG_KEY = "globals"
UPDATE_CFG_KEY = "secupdate"
ID_CFG_KEY = "id"
PUBLIC_KEY_CFG_ID = "public-key-file"
WRITE_CFG_KEY = "write_cfg_path"
DAY_CFG_KEY = "day"
HOURS_CFG_KEY = "hours"

SWUPDATE = "swupdate"
SWUPDATE_CLIENT = "swupdate-client"
IMAGE = "image"
DEVICE_LED_FAILED = "failed"
DEVICE_LED_RESET = "reset"

SW_CONF_FILE_PATH = "/etc/swupdate.cfg"
SW_VERSION_FILE_PATH = "/var/sw-versions"
LAIRD_RELEASE_FILE_PATH = "/etc/os-release"

kernel_side = {"a": "/dev/ubi0_0", "b": "/dev/ubi0_3"}
rootfs_side = {"a": "/dev/ubi0_1", "b": "/dev/ubi0_4"}

components_dict = {"kernel": kernel_side, "rootfs": rootfs_side}

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
SWUPDATE_SUCCESS = "2"
SWUPDATE_FAILED = "3"


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
        self.device_svc = None
        self.updated_component = set()
        self.gen_sw_version()
        self.get_wlan_hw_address()
        self.conn_device_service()
        self.local_update = LocalUpdate(
            self.process_config, self.initiate_swupdate, self.device_svc
        )
        self.update_state = UPDATE_READY
        self.device_name_prefix = "Laird_"
        self.write_cfg_path = "/data/public/igupd/update_schedule.conf"
        self.public_key_file = None
        self.sslkey = None
        self.process_config()

        if get_uboot_env_value(UPGRADE_AVAILABLE) == "1":
            set_env(UPGRADE_AVAILABLE, "0")
            set_env(BOOTCOUNT, "0")
        self.swupdate_client = swuclient.SWUpdateClient(self.swupdate_handler)

    def get_wlan_hw_address(self):
        bus = dbus.SystemBus()
        nm = dbus.Interface(bus.get_object(NM_IFACE, NM_OBJ), NM_IFACE)
        wifi_dev_obj = bus.get_object(NM_IFACE, nm.GetDeviceByIpIface("wlan0"))
        wifi_dev_props = dbus.Interface(wifi_dev_obj, DBUS_PROP_IFACE)
        self.mac_addr = str(wifi_dev_props.Get(NM_WIFI_DEVICE_IFACE, "HwAddress"))
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
            with open(LAIRD_RELEASE_FILE_PATH, "r") as f2:
                for line in f2:
                    if line.startswith("VERSION_ID="):
                        line = line.rstrip("\n")
                        words = line.split("=")
                        if len(words) > 1:
                            laird_version = words[1]
                        else:
                            laird_version = 0
                syslog("igupd:gen_sw_version: laird {}".format(laird_version))
                with open(SW_VERSION_FILE_PATH, "w") as f1:
                    for key, value in components_dict.items():
                        if key == "kernel" or key == "rootfs":
                            f1.write("{}  {}\n".format(key, laird_version))
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

    def update_available(self):
        """
        Reset the update_available uboot var to '1'.  Set the 'bootcmd' and
        'bootargs' vars to the new setting. Schedule reboot.
        """
        self.schedule_reboot(self.config.get(UPDATE_SCHEDULE))
        self.UpdatePending(UPDATE_SCHEDULED)
        self.update_state = UPDATES_AVAILABLE

    def check_update(self, perform_update):
        return self.update_state

    def swupdate_handler(self, status, curr_img, msg):
        """
        Receive handler for swupdate.  Process the signals and keep
        track of the state.
        """
        if curr_img:
            self.updated_component.add(curr_img)

        if status == swuclient.SWU_STATUS_START:
            if self.usb_local_update is True and self.device_svc:
                self.device_svc.DeviceUpdating()

            self.UpdatePending(UPDATE_DOWNLOADING)
            self.update_state = UPDATES_IN_PROGRESS

        elif status == swuclient.SWU_STATUS_SUCCESS:
            if self.updated_component:
                if (
                    "kernel.itb" in self.updated_component
                    and "rootfs.bin" in self.updated_component
                ):
                    for keys in self.updated_component:
                        syslog("swupdate_handler: Components updated are : %s" % keys)
                    self.switch_side = True
                self.update_available()
                self.updated_component.clear()
            else:
                # case when update is skipped
                self.update_state = NO_UPDATE_AVAILABLE
                self.updated_component.clear()
                if self.usb_local_update is True:
                    self.local_update_state_change(DEVICE_LED_RESET)

        elif status == swuclient.SWU_STATUS_FAILURE:
            self.update_state = NO_UPDATE_AVAILABLE
            self.updated_component.clear()
            if self.usb_local_update is True:
                self.local_update_state_change(DEVICE_LED_FAILED)

        elif status == swuclient.SWU_STATUS_BAD_CMD:
            self.updated_component.clear()
            if self.usb_local_update is True:
                self.local_update_state_change(DEVICE_LED_FAILED)

    def process_config(self, config=None):
        """
        If a config is passed, with update_schedule information, then update
        runtime self.config and write to a persistant memory in /data

        if a config is passed, this can also be a complete config, only for local
        update seperately

        If a config is not passed, read the configuration from /rodata/
        and override update_schedule if the file exists in /data/
        """

        if config is None:
            try:
                c = {}
                with open(SW_CONF_FILE_PATH, "r") as f:
                    c = libconf.load(f)
                if GLOBALS_CFG_KEY in c:
                    g = c[GLOBALS_CFG_KEY]
                    if ID_CFG_KEY in g:
                        self.device_name_prefix = g[ID_CFG_KEY]
                    if PUBLIC_KEY_CFG_ID in g:
                        self.public_key_file = g[PUBLIC_KEY_CFG_ID]
                self.device_name = self.device_name_prefix + self.mac_addr
                syslog("Secure update device ID: " + self.device_name)
                u = c.get(UPDATE_CFG_KEY, {})
                if WRITE_CFG_KEY in u:
                    self.write_cfg_path = u[WRITE_CFG_KEY]
                syslog("Secure update config write path: " + self.write_cfg_path)
                # Convert update_schedule from cfg format to dict
                update_schedule = []
                if UPDATE_SCHEDULE in u:
                    sched = u[UPDATE_SCHEDULE]
                    for e in sched:
                        if DAY_CFG_KEY in e and HOURS_CFG_KEY in e:
                            day_str = e[DAY_CFG_KEY]
                            hours_str = e[HOURS_CFG_KEY]
                            update_schedule.append({day_str: hours_str})
                if check_schedule(update_schedule):
                    self.config[UPDATE_SCHEDULE] = update_schedule

                # Override update_schedule if config file exists
                update_schedule = load_schedule(self.write_cfg_path, UPDATE_SCHEDULE)
                if update_schedule:
                    self.config[UPDATE_SCHEDULE] = update_schedule
                    syslog("Update schedule: {}".format(self.config[UPDATE_SCHEDULE]))
            except (RuntimeError, IOError):
                syslog(
                    "Failed to parse secure update configuration file: {}".format(
                        traceback.format_exc()
                    )
                )
                return False
        else:
            # if local update
            if IMAGE in config:
                self.config = config
            # if no local update but schedule info in config
            elif self.config is not None:
                ret = False
                try:
                    if check_schedule(config.get(UPDATE_SCHEDULE)):
                        self.config[UPDATE_SCHEDULE] = config[UPDATE_SCHEDULE]
                        save_schedule(
                            self.write_cfg_path,
                            UPDATE_SCHEDULE,
                            self.config[UPDATE_SCHEDULE],
                        )
                        syslog(
                            "igupd: process_config: update schedule modified successfully: {}".format(
                                self.config[UPDATE_SCHEDULE]
                            )
                        )
                        ret = True
                    return ret
                except (TypeError, AttributeError, ValueError):
                    return False
        return True

    def initiate_swupdate(self, local_update=False):
        syslog("Initiating update")

        # Check the current boot side so we can make the appropriate switch later
        if self.current_boot_side == "a":
            syslog("Current boot_side is a")
            select = "stable,main-b"
        else:
            syslog("Current boot_side is b")
            select = "stable,main-a"
        self.usb_local_update = local_update
        cmd = [
            SWUPDATE_CLIENT,
            "-e",
            select,
            self.config[IMAGE],
        ]
        self.swupdate_client.initiate_swupdate(cmd)

    def schedule_reboot(self, update_list):
        """
        Parse the update schedule to determine the delta of seconds
        until the next update window.
        """
        now = datetime.datetime.now()
        delta_start, delta_end = next_schedule_window(now, update_list)
        """
        Start the reboot timer.  The snooze command will use this timer
        to snooze the reboot
        """
        syslog("Rebooting in {} seconds.".format(delta_start))
        self.reboot_timer = resumetimer.ResumableTimer(delta_start, self.reboot)
        self.reboot_timer.start()
        self.UpdatePending(UPDATE_SCHEDULED)

    def snooze_reboot(self, snooze_seconds):
        """
        If a reboot is schedule, stall the reboot for the specified time
        """
        # No update if the reboot_timer isn't instantiated
        if self.reboot_timer is None:
            return -1

        ret = self.reboot_timer.pause(snooze_seconds)
        if ret == 0:  # Snooze
            self.UpdatePending(UPDATE_SNOOZED)
        return ret

    def reboot(self):
        """
        Use the IG's reboot command to initiate the reboot
        """

        data_migrate_success = True

        if self.switch_side:
            if os.path.exists("/usr/sbin/migrate_data.sh"):
                data_migrate_success = data_migration()

            if data_migrate_success:
                if self.current_boot_side == "a":
                    set_env(BOOTSIDE, "b")
                    set_env(ALTBOOTCMD, "setenv bootside a; saveenv; run bootcmd")
                else:
                    set_env(BOOTSIDE, "a")
                    set_env(ALTBOOTCMD, "setenv bootside b; saveenv; run bootcmd")

        if data_migrate_success:
            self.UpdatePending(UPDATE_REBOOT)
            set_env(UPGRADE_AVAILABLE, "1")
            set_env(BOOTLIMIT, "5")
            reboot()
        else:
            self.switch_side = False
            self.update_state = NO_UPDATE_AVAILABLE
            self.UpdatePending(UPDATE_FAILED)
            self.updated_component.clear()
            self.reboot_timer = None
            if self.usb_local_update is True:
                self.local_update_state_change(DEVICE_LED_FAILED)

    def local_update_state_change(self, handler):
        if handler == DEVICE_LED_RESET and self.device_svc:
            self.device_svc.DeviceUpdateReset()
        elif handler == DEVICE_LED_FAILED and self.device_svc:
            self.device_svc.DeviceUpdateFailed()
        self.usb_local_update = False
        self.process_config()
