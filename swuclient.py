import socket
import sys
import struct
import threading
import random
import subprocess
import time
import os
from syslog import syslog, openlog

SWU_STATUS_IDLE=0
SWU_STATUS_START=1
SWU_STATUS_RUN=2
SWU_STATUS_SUCCESS=3
SWU_STATUS_FAILURE=4
SWU_STATUS_DOWNLOAD=5
SWU_STATUS_DONE=6
SWU_STATUS_SUBPROCESS=7

SWU_PROG_ADDRESS = '/tmp/swupdateprog'

class SWUpdateClient(threading.Thread):
    def __init__(self,handler,cmd):
        threading.Thread.__init__(self)
        self.recv_handler = handler
        self.proc = None
        self.cmd = cmd

    def connect_to_prog_sock(self):
        timeout = time.time() + 5
        while True:
            try: 
                if os.path.exists(SWU_PROG_ADDRESS):
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.connect(SWU_PROG_ADDRESS)
                    return True
                if time.time() > timeout:
                    return False
            except socket.error, exc:
                print "Caught exception socket.error : %s" % exc

    def receive_progress_updates(self):
        while True:
            try:
                data = self.sock.recv(2400)
                if not data:
                    break
                fields = struct.unpack('=IiIIII256s64siI2048s', data)
                self.progress_handler(fields[1],fields[10])
            except socket.error, exc:
          
        self.sock.close()


    def start_swupdate(self):
        self.proc = subprocess.Popen(self.cmd, shell=False)

        if self.connect_to_prog_sock():
            self.receive_progress_updates()

        if self.proc.poll() == None:)
            self.proc.kill()

    def restart_swupdate(self):
        if self.proc.poll() == None:
            self.proc.terminate()

    def run(self):
        while True:
            self.start_swupdate()
            time.sleep(3)

    def progress_handler(self,status,msg):
        self.state = status
        self.recv_handler(status,msg)

    def get_state(self):
        return self.state

    def set_command(self,cmd):
        self.cmd = cmd


