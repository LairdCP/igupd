import dbus, dbus.service, dbus.exceptions
import sys, signal
from syslog import syslog, openlog
from dbus.mainloop.glib import DBusGMainLoop
import gobject
import swupd
import daemon
import random

# Global loop object
loop = None

def main():
    # Initialize a main loop
    DBusGMainLoop(set_as_default=True)
    gobject.threads_init()
    loop = gobject.MainLoop()

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
        interval = random.randint(1,30)
        gobject.timeout_add(2000, update_service.check_sw_update)
        gobject.timeout_add(interval*1000, update_service.attempt_update)
        loop.run()
    except KeyboardInterrupt:
        syslog("Received signal, shutting down service.")
    except Exception as e:
        syslog("Unexpected exception occurred: '{}'".format(str(e)))
    finally:
        loop.quit()
    return 0

#
# Run the main loop in daemon context
#
with daemon.DaemonContext(
    stdout=sys.stdout,
    stderr=sys.stderr):
    openlog("IG.UpdateService")
    syslog("Starting main loop.")
    main()
