import os
import sys
import subprocess
import re
from syslog import syslog, openlog
from threading import Timer

CMD_FW_PRINTENV = "fw_printenv"
CMD_FW_SETENV = "fw_setenv"
CMD_BOOTSIDE = "bootside"
CMD_REBOOT = "reboot"
CMD_MD5SUM = "md5sum"


def run_proc(cmd, timeout=5):
    '''
    Run the given process or cmd.  Use a timer in case
    the process does not return.  Kill after timeout.
    '''
    kill = lambda process: process.kill()
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    my_timer = Timer(timeout, kill, [proc])
    try:
        my_timer.start()
        stdout, stderr = proc.communicate()
        syslog(stdout)
    finally:
        my_timer.cancel()

    return stdout, stderr


def run_proc_async(cmd):
    '''
    Same as run_proc, except run asynchronously
    '''
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    finally:
        proc.kill()


def get_uboot_env_value(var):
    '''
    Run the 'fw_printenv' command and return the value
    of the given u-boot environment variable
    '''
    cmd = [CMD_FW_PRINTENV]
    out, err = run_proc(cmd)

    m = re.search(var+'=([a-zA-Z0-9]*)\n', out)
    if m:
        return m.group(1)
    else:
        return None


def get_current_side():
    '''
    Return the current bootside
    '''
    val = get_uboot_env_val(CMD_BOOTSIDE)
    return val


def set_env(var, value):
    '''
    Set a u-boot environment variable
    '''
    out, err = run_proc([CMD_FW_SETENV, var, value])
    if err == 0:
        return True
    else:
        return False


def generate_md5sum(partition):
    '''
    Handler to generate md5sum for each components
    '''
    out, err = run_proc([CMD_MD5SUM, partition], timeout=20)
    if err and (out is None):
        return -1
    else:
        md5sum, file = out.split()
        return md5sum


def reboot():
    '''
    Call the 'reboot' command
    '''
    cmd = [CMD_REBOOT]
    out, err = run_proc([CMD_REBOOT],300)

    return err
