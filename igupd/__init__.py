import dbus, dbus.service, dbus.exceptions
from syslog import syslog, openlog
from dbus.mainloop.glib import DBusGMainLoop
import traceback

from . import swupd

from gi.repository import GObject as gobject
from gi.repository import GLib as glib

# Global loop object
loop = None

def main():
    openlog("IG.UpdateService")
    syslog("Starting main loop.")

    # Initialize a main loop
    DBusGMainLoop(set_as_default=True)
    gobject.threads_init()

    loop = glib.MainLoop()

    # Declare a name where our service can be reached
    try:
        bus_name = dbus.service.BusName("com.lairdtech.security.UpdateService",
                                        bus=dbus.SystemBus(),
                                        do_not_queue=True)
    except dbus.exceptions.NameExistsException:
        syslog("service is already running")
        return 1

    syslog('Starting software update service')

    # Run the loop
    try:
        # Create our initial update service object, and run the GLib main loop
        update_service = swupd.SoftwareUpdate(bus_name)
        loop.run()
    except KeyboardInterrupt:
        syslog("Received signal, shutting down service.")
    except Exception as e:
        syslog("Unexpected exception occurred: '{}'".format(traceback.format_exc()))
    finally:
        loop.quit()
