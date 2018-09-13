import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from syslog import syslog, openlog

IG_PROV_IFACE = 'com.lairdtech.IG.ProvService'
IG_PROV_OBJ = '/com/lairdtech/IG/ProvService'

NM_IFACE =            'org.freedesktop.NetworkManager'
NM_SETTINGS_IFACE =   'org.freedesktop.NetworkManager.Settings'
NM_SETTINGS_OBJ =     '/org/freedesktop/NetworkManager/Settings'
NM_OBJ =              '/org/freedesktop/NetworkManager'
NM_CONNECTION_IFACE = 'org.freedesktop.NetworkManager.Settings.Connection'
NM_DEVICE_IFACE =     'org.freedesktop.NetworkManager.Device'
DBUS_PROP_IFACE =     'org.freedesktop.DBus.Properties'

'''
Checks for a successful boot by verifying the services are running
correctly and the wifi interface is up.  Watchdog is handled in by
lower level parts of the system.
'''

def check_ig_services():
    try:
        bus = dbus.SystemBus()
        prov_service = bus.get_object(IG_PROV_IFACE, IG_PROV_OBJ)
        return True
    except dbus.exceptions.DBusException as e:
        syslog("Failed to initialize D-Bus object: '%s'" % str(e))
        return False

def check_wifi_interface():
    bus = dbus.SystemBus()
    nm_service = bus.get_object(NM_IFACE, NM_OBJ)
    manager = dbus.Interface(nm_service, NM_IFACE)
    devices = manager.GetDevices()
    for d in devices:
        dev_proxy = bus.get_object(NM_IFACE, d)
        prop_iface = dbus.Interface(dev_proxy, DBUS_PROP_IFACE)
        name = prop_iface.Get(NM_DEVICE_IFACE, "Interface")
        if name == "wlan0":
            return True
    syslog("Couldn't find Wlan0")
    return False
