'''TO-DO: Implement check if usb is inserted while booting and trigger swupdate depends on if device is reboot from rollback
          or already updated with the package in local usb update'''

import os
from psutil import disk_partitions
from syslog import syslog
from pyudev.glib import MonitorObserver
from pyudev import Context, Monitor

import sys
PYTHON3 = sys.version_info >= (3, 0)
if PYTHON3:
    from gi.repository import GObject as gobject
else:
    import gobject

MOUNT_POINT_PATH = "/media/sda1"
DEVICE = "sda"
DEVICE_PART1 = "/dev/sda1"
UPDATE_PACKAGE_NAME = "swupdate.swu"
RETRY_COUNT = 8
TIMEOUT = 2000

local_update_config = { "blacklist" : "0 1 2 3", "select" : "stable,main", "image" : None, "update_schedule": [{"*" : "0-24"}] }


class LocalUpdate:
    """
    Local software update through USB
    """
    def __init__(self, callback1, callback2, device_svc):
        self.device_svc = device_svc
        self.process_config = callback1
        self.start_swupdate = callback2
        self.retry_count = 0
        self.job_id = None
        self.start_usb_detection()
        syslog("usbupd: init: Local USB Update is initialized")

    def check_mount_point(self):
        """
        Function looks for mount point and sends the swupdate config to Softwareupdate service
        """
        try:
            if self.retry_count == RETRY_COUNT:
                self.retry_count = 0
                return False

            for partition in disk_partitions(all=False):
                if DEVICE_PART1 in partition.device:
                    update_path = os.path.join(partition.mountpoint, UPDATE_PACKAGE_NAME)
                    if os.path.isfile(update_path):
                        local_update_config["image"] = update_path
                        syslog("usbupd: check_mount_point: Starting config parsing...")
                        ret = self.process_config(local_update_config)
                        if ret:
                            syslog("usbupd: check_mount_point: Starting Software Update...")
                            gobject.source_remove(self.job_id)
                            self.start_swupdate(False)
                            self.retry_count = 0
                    self.job_id = None
                    return False

            self.retry_count += 1
            return True
        except Exception as e:
            syslog("usbupd: check_mount_point: %s" % e)
            if self.job_id is not None:
                gobject.source_remove(self.job_id)
                self.job_id = None
            self.retry_count = 0

    def device_event(self,observer, device):
        """
        USB Device Event signal
        """
        try:

            if device.action == "add":
                syslog("usbupd: device_event: USB inserted")
                if self.job_id is None:
                    self.job_id = gobject.timeout_add(TIMEOUT,self.check_mount_point)

            if device.action == "remove":
                syslog("usbupd: device_event: USB removed")
                # reset device leds
                if self.device_svc:
                    self.device_svc.DeviceUpdateReset()
                if self.retry_count != 0:
                    self.retry_count = 0

                if self.job_id is not None:
                    gobject.source_remove(self.job_id)
                    self.job_id = None

        except Exception as e:
            syslog("usbupd: device_event: %s" % e)

            if self.job_id is not None:
                gobject.source_remove(self.job_id)
                self.job_id = None

            self.start_usb_detection()

    def start_usb_detection(self):
        """
        Starts listening to udev events for usb activity
        """
        try:
            #Remove comments to enable usb local update on boot
            #if os.path.exists(DEVICE_PART1):
            #    syslog("start_usb_detection: Mount point exists")
            #    self.check_mount_point()
            context = Context()
            monitor = Monitor.from_netlink(context)
            monitor.filter_by(subsystem='usb')
            observer = MonitorObserver(monitor)
            observer.connect('device-event', self.device_event)
            monitor.start()
        except Exception as e:
            syslog("usbupd:start_usb_detection: %s" % e)
