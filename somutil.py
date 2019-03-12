import os
import sys
import subprocess
import hashlib
import re
from syslog import syslog, openlog
from threading import Timer

CMD_FW_PRINTENV = "fw_printenv"
CMD_FW_SETENV = "fw_setenv"
CMD_BOOTSIDE = "bootside"
CMD_REBOOT = "reboot"
CMD_MD5SUM = "md5sum"
CMD_MIGRATE_DATA = "migrate_data.sh"
CMD_STTY = 'stty'
CMD_OPENSSL = 'openssl'
SERIAL_DEVICE = '/dev/ttyS0'

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
        if stdout:
            decoded = stdout.decode('utf-8')
            syslog(decoded)
        else:
            decoded = None
    except Exception as e:
        syslog("Failed to run proc: '%s'" % str(e))
    finally:
        my_timer.cancel()

    return decoded, stderr


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
    if out:
        m = re.search(var+'=([a-zA-Z0-9]*)\n', out)
        if m:
            return m.group(1)
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


def data_migration():
    '''
    Handler to migrate data between two sides
    '''
    syslog("igupd: data_migration: Starting")
    out, err = run_proc([CMD_MIGRATE_DATA], timeout=40)
    if err:
        syslog("igupd: data_migration: Data migration failed")
        return False
    else:
        syslog("igupd: data_migration: Data migration Completed")
        return True


def generate_md5sum(partition):
    '''
    Handler to generate md5sum for each components
    '''
    hash_md5 = hashlib.md5()
    with open(partition, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def boot_successful(key_path):
    '''
    Print successful boot message
    '''
    #validate certificate file
    out, err = run_proc([CMD_OPENSSL ,'x509', '-in', key_path, '-noout'])
    syslog("igupd: boot_successful: openssl -> out : {} err: {}".format(out, err))
    if not err:
        #set the baudrate
        out, err = run_proc([CMD_STTY, '-F', SERIAL_DEVICE, 'speed', '115200'])
        syslog("igupd: boot_successful: stty out : {} err: {}".format(out, err))
        if not err:
            with open(SERIAL_DEVICE, "w") as f:
                f.write("Secure Boot Cycle Complete")


def reboot():
    '''
    Call the 'reboot' command
    '''
    cmd = [CMD_REBOOT]
    out, err = run_proc([CMD_REBOOT],300)

    return err
