#!/usr/bin/env python

import argparse
import dbus, dbus.exceptions, dbus.mainloop.glib
import threading
import time
import unittest

import sys
PYTHON3 = sys.version_info >= (3, 0)
if PYTHON3:
    from gi.repository import GObject as gobject
else:
    import gobject

NO_UPDATE_AVAILABLE =    0
UPDATES_AVAILABLE =      1
UPDATES_IN_PROGRESS =     2
CHECK_ABORTED =          -1

UPDATE_SNOOZED =        0
UPDATE_DOWNLOADING =    1
UPDATE_SCHEDULED =      2
UPDATE_REBOOT =         3
UPDATE_READY =          4

config_string_good = '{ "blacklist" : "0 1 2 3", "select" : "stable,main", "download" : { \
                        "url" : "http://10.1.40.234:8000/ig60_20180713.swu" }, "update_schedule" : [{ "*" : "16-17" }]}'
config_string_bad = '{ {"blacklist" : "0 1 2 3", "select" : "stable,main", "download" : { \
                        "url" : "http://10.1.40.234:8000/ig60_20180713.swu" }, "update_schedule" : [{ "*" : "14-17" }]}'

class SoftwareUpdateTestCase(unittest.TestCase):
    def setUp(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        try:
            bus = dbus.SystemBus()
            self.signal = None
            self.update_service = bus.get_object("com.lairdtech.security.UpdateService", "/com/lairdtech/security/UpdateService")
            self.update_service.connect_to_signal(signal_name="UpdatePending", handler_function=self.set_signal)
            self.loop = gobject.MainLoop()
        except dbus.exceptions.DBusException as e:
            print("Failed to initialize D-Bus object: '%s'" % str(e))
            sys.exit(2)

    def tearDown(self):
        self.loop.quit()

    def set_signal(self,signal):
        self.signal = signal

    def check_signal(self,signal):
        self.assertEqual(signal,self.signal)

    def test_set_configuration(self,config,expected):
        ret = self.update_service.SetConfiguration(config_string_good)
        self.assertEqual(ret,expected)

    def test_check_update(self,perform,expected):
        ret = self.update_service.CheckUpdate(perform)
        self.assertEqual(ret,expected)

    def test_snooze(self, snooze_seconds, expected):
        ret = self.update_service.SnoozeUpdate(snooze_seconds)
        self.assertEqual(ret,expected)

    def test_update(self):
        gobject.timeout_add(2000, self.test_set_configuration, config_string_good, 0)
        gobject.timeout_add(3000, self.test_check_update, True, UPDATES_IN_PROGRESS)
        gobject.timeout_add(5000, self.check_signal, UPDATE_DOWNLOADING)
        gobject.timeout_add(30000, self.check_signal, UPDATE_SCHEDULED)
        gobject.timeout_add(35000, self.test_snooze, 300, 0)
        gobject.timeout_add(40000, self.check_signal, UPDATE_SNOOZED)
        loop = gobject.MainLoop()
        loop.run()

if __name__ == '__main__':
    unittest.main()
