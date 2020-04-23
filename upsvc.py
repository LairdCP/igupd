#
# UpdateService.py - Main Dbus update service interface
#
import dbus.service
import dbus.exceptions
import time
import threading
import json
from syslog import syslog
#
# Provisioning status/states
#
PROV_COMPLETE_SUCCESS = 0
PROV_UNPROVISIONED = 1
PROV_INPROGRESS_DOWNLOADING = 2
PROV_INPROGRESS_APPLYING = 3
PROV_FAILED_INVALID = -1
PROV_FAILED_CONNECT = -2
PROV_FAILED_AUTH = -3
PROV_FAILED_TIMEOUT = -4
PROV_FAILED_NOT_FOUND = -5

class UpdateService(dbus.service.Object):
    def __init__(self, bus_name):
        super(UpdateService, self).__init__(bus_name, "/com/lairdtech/security/UpdateService")

    @dbus.service.method("com.lairdtech.security.UpdateInterface",
                         in_signature='s', out_signature='i')
    def SetConfiguration(self, config):
        try:
            update_config = json.loads(config)
        except Exception as e:
            syslog("Configuration failed to load JSON, exception = %s" % str(e))
            return -1

        if self.process_config(update_config):
            return 0
        else:
            return -1

    @dbus.service.method("com.lairdtech.security.public.UpdateInterface",
                         in_signature='b', out_signature='i')
    def CheckUpdate(self, perform_update):
        return self.check_update(perform_update)

    @dbus.service.method("com.lairdtech.security.public.UpdateInterface",
                         in_signature='i', out_signature='i')
    def SnoozeUpdate(self, snooze_seconds):
        return self.snooze_reboot(snooze_seconds)

    @dbus.service.signal("com.lairdtech.security.public.UpdateInterface", signature='i')
    def UpdatePending(self, update_action):
        return update_action

    @dbus.service.method(dbus.PROPERTIES_IFACE,
                         in_signature='ss', out_signature='v')
    def Get(self, interface_name, property_name):
        return self.GetAll(interface_name)[property_name]

    @dbus.service.method(dbus.PROPERTIES_IFACE,
                         in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface_name):
        if interface_name == "com.lairdtech.security.public.UpdateInterface":
            return { 'Status' : self.result }
        else:
            raise dbus.exceptions.DBusException(
                'com.lairdtech.UnknownInterface',
                'The UpdateService does not implement the %s interface'
                    % interface_name)
