import socket
import select
import struct
import json
import threading
import random
import subprocess
import time
import os
from syslog import syslog, openlog

import sys
PYTHON3 = sys.version_info >= (3, 0)

SWU_STATUS_IDLE=0
SWU_STATUS_START=1
SWU_STATUS_RUN=2
SWU_STATUS_SUCCESS=3
SWU_STATUS_FAILURE=4
SWU_STATUS_DOWNLOAD=5
SWU_STATUS_DONE=6
SWU_STATUS_SUBPROCESS=7
SWU_STATUS_BAD_CMD=8

SIGNAL_KILL = -9
SIGNAL_TERM = -15

SWUPDATE_MAGIC = 0x14052001
SWUPDATE_MSG_SUBPROCESS = 5
SWUPDATE_CMD_ENABLE = 2
SWUPDATE_SRC_SURICATTA = 2
SURICATTA_CONNECT_ATTEMPTS = 5
SURICATTA_CONNECT_DELAY = 5
SURICATTA_RESPONSE_TIMEOUT = 2

SWUPDATE_MSG_STRUCT = 'IiiiiI2048s'

SWU_PROG_ADDRESS = '/tmp/swupdateprog'
SWU_CTRL_ADDRESS = '/tmp/sockinstctrl'

class SWUpdateClient(threading.Thread):
    def __init__(self,handler,cmd):
        self.recv_handler = handler
        self.proc = None
        self.cmd = cmd
        threading.Thread.__init__(self)

    def connect_to_prog_sock(self):
        timeout = time.time() + 10
        while True:
            try:
                if os.path.exists(SWU_PROG_ADDRESS):
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.connect(SWU_PROG_ADDRESS)
                    syslog("Socket connection established")
                    return True
                if time.time() > timeout:
                    return False
            except socket.error as exc:
                syslog("Caught exception socket.error.. Retrying: %s" % exc)
                time.sleep(2)

    def receive_progress_updates(self):
        while True:
            try:
                data = self.sock.recv(2400)
                if not data:
                    break
                fields = struct.unpack('=IiIIII256s64siI2048s', data)
                if PYTHON3:
                    self.progress_handler(fields[1], str(fields[6],"utf-8"), str(fields[10],"utf-8"))
                else:
                    self.progress_handler(fields[1], fields[6], fields[10])
            except socket.error as exc:
                syslog("Caught exception socket.error: %s" % exc)
            except Exception as e:
                syslog("Failed to do progress updates: '%s'" % str(e))

        self.sock.close()


    def start_swupdate(self):
        self.proc = subprocess.Popen(self.cmd, shell=False)

        if self.connect_to_prog_sock():
            self.receive_progress_updates()

        (out, err) = self.proc.communicate()

        if self.proc.returncode != 0:
            if self.proc.returncode == SIGNAL_TERM:
                syslog("Subprocess was terminated by SIGTERM %d" % (self.proc.returncode))
            elif self.proc.returncode == SIGNAL_KILL:
                syslog("Subprocess was terminated by SIGKILL %d" % (self.proc.returncode))
            else:
                syslog("command failed stopping, exit-code=%d" % (self.proc.returncode))
                self.progress_handler(SWU_STATUS_BAD_CMD, None, self.proc.returncode)

        if self.proc.poll() is None:
            self.proc.kill()

    def restart_swupdate(self):
        if self.proc.poll() is None:
            self.proc.terminate()

    def run(self):
        while True:
            self.start_swupdate()
            time.sleep(3)

    def progress_handler(self, status, curr_image, msg):
        self.state = status
        if curr_image:
            rcurr_img = curr_image.strip('\x00')
        else:
            rcurr_img = None
        self.recv_handler(status, rcurr_img, msg)

    def get_state(self):
        return self.state

    def set_command(self,cmd):
        self.cmd = cmd

    def suricatta_enable(self, enable):
        json_msg = json.dumps({'enable' : enable})
        msg = struct.pack(SWUPDATE_MSG_STRUCT,
            SWUPDATE_MAGIC,
            SWUPDATE_MSG_SUBPROCESS,
            SWUPDATE_SRC_SURICATTA,
            SWUPDATE_CMD_ENABLE,
            0,
            len(json_msg),
            json_msg.encode('utf8'))
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            # Use retries when connecting, as the daemon socket may
            # not be ready yet
            connect_try = 0
            while True:
                if s.connect_ex(SWU_CTRL_ADDRESS) == 0:
                    break
                connect_try = connect_try + 1
                if connect_try > SURICATTA_CONNECT_ATTEMPTS:
                    syslog('Suricatta socket connect failed.')
                    return
                time.sleep(SURICATTA_CONNECT_DELAY)
            s.send(msg)
            rd, wr, ex = select.select([s], [], [], SURICATTA_RESPONSE_TIMEOUT)
            if s in rd:
                syslog('Suricatta {}able message successful.'.format('en' if enable else 'dis'))
            else:
                syslog('Suricatta socket response timed out.')
        except socket.error as e:
            syslog('Suricatta socket error occurred: {}'.format(e))
        finally:
            s.close()
