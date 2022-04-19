import socket
import select
import struct
import json
import threading
import subprocess
import time
import os
from syslog import syslog

SWU_STATUS_IDLE = 0
SWU_STATUS_START = 1
SWU_STATUS_RUN = 2
SWU_STATUS_SUCCESS = 3
SWU_STATUS_FAILURE = 4
SWU_STATUS_DOWNLOAD = 5
SWU_STATUS_DONE = 6
SWU_STATUS_SUBPROCESS = 7
SWU_STATUS_BAD_CMD = 8

SIGNAL_KILL = -9
SIGNAL_TERM = -15

SWUPDATE_MAGIC = 0x14052001
SWUPDATE_MSG_SUBPROCESS = 5
SWUPDATE_CMD_ENABLE = 2
SWUPDATE_SRC_SURICATTA = 2
SURICATTA_CONNECT_DELAY = 60
SURICATTA_RESPONSE_TIMEOUT = 2

SWUPDATE_MSG_STRUCT = "IiiiiI2048s"

SWU_PROG_ADDRESS = "/tmp/swupdateprog"
SWU_CTRL_ADDRESS = "/tmp/sockinstctrl"


class SWUpdateClient(threading.Thread):
    def __init__(self, handler, cmd):
        self.recv_handler = handler
        self.proc = None
        self.cmd = cmd
        self.suricatta_pending_enable = None
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
                fields = struct.unpack("=IiIIII256s64siI2048s", data)
                self.progress_handler(
                    fields[1], str(fields[6], "utf-8"), str(fields[10], "utf-8")
                )
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
                syslog(
                    "Subprocess was terminated by SIGTERM %d" % (self.proc.returncode)
                )
            elif self.proc.returncode == SIGNAL_KILL:
                syslog(
                    "Subprocess was terminated by SIGKILL %d" % (self.proc.returncode)
                )
            else:
                syslog("command failed stopping, exit-code=%d" % (self.proc.returncode))
                self.progress_handler(SWU_STATUS_BAD_CMD, None, self.proc.returncode)

        if self.proc.poll() is None:
            self.proc.kill()

    def restart_swupdate(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def run(self):
        while True:
            self.start_swupdate()
            time.sleep(3)

    def progress_handler(self, status, curr_image, msg):
        self.state = status
        if curr_image:
            rcurr_img = curr_image.strip("\x00")
        else:
            rcurr_img = None
        self.recv_handler(status, rcurr_img, msg)

    def get_state(self):
        return self.state

    def set_command(self, cmd):
        self.cmd = cmd

    def send_suricatta_enable(self):
        # Suricatta is not always started when swupdate begins;
        # for example, if swupdate is attempting to report status
        # to HawkBit after an update, it must establish the initial
        # response before the suricatta socket is available to
        # enable downloads.  There is no notification via the status
        # socket (sigh), so we must continually attempt to connect
        # until it responds.
        json_msg = json.dumps({"enable": self.suricatta_pending_enable})
        msg = struct.pack(
            SWUPDATE_MSG_STRUCT,
            SWUPDATE_MAGIC,
            SWUPDATE_MSG_SUBPROCESS,
            SWUPDATE_SRC_SURICATTA,
            SWUPDATE_CMD_ENABLE,
            0,
            len(json_msg),
            json_msg.encode("utf8"),
        )
        syslog(
            "Attempting to send suricatta {}able message.".format(
                "en" if self.suricatta_pending_enable else "dis"
            )
        )
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(SWU_CTRL_ADDRESS)
            s.send(msg)
            rd, wr, ex = select.select([s], [], [], SURICATTA_RESPONSE_TIMEOUT)
            if s in rd:
                syslog(
                    "Suricatta {}able message successful.".format(
                        "en" if self.suricatta_pending_enable else "dis"
                    )
                )
                self.suricatta_pending_enable = None
                s.close()
                return
            else:
                s.close()
                syslog("Suricatta socket response timed out.")
        except socket.error as e:
            syslog("Suricatta socket error occurred: {}".format(e))

        # Request to suricatta was not successful, try again later.
        threading.Timer(SURICATTA_CONNECT_DELAY, self.send_suricatta_enable).start()

    def suricatta_enable(self, enable):
        if self.suricatta_pending_enable is not None:
            # There is already a pending value to send, just change it
            self.suricatta_pending_enable = enable
        else:
            # Attempt to send enable message to suricatta socket
            self.suricatta_pending_enable = enable
            self.send_suricatta_enable()
