import subprocess
import hashlib
import re
from syslog import syslog

CMD_FW_PRINTENV = "fw_printenv"
CMD_FW_SETENV = "fw_setenv"
CMD_BOOTSIDE = "bootside"
CMD_REBOOT = "reboot"
CMD_MD5SUM = "md5sum"
CMD_MIGRATE_DATA = "migrate_data.sh"
CMD_STTY = "stty"
CMD_OPENSSL = "openssl"
SERIAL_DEVICE = "/dev/ttyS0"


def run_proc(cmd, timeout=5):
    """
    Run the given process or cmd.  Use a timer in case
    the process does not return.  Kill after timeout.
    """

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = proc.stdout.decode("utf-8")
        if out:
            syslog(out)
        return (proc.returncode == 0, out)
    except Exception as e:
        syslog("Failed to run proc: '%s'" % str(e))
        return (False, "")


def get_uboot_env_value(var):
    """
    Run the 'fw_printenv' command and return the value
    of the given u-boot environment variable
    """
    res, out = run_proc([CMD_FW_PRINTENV])
    if res:
        m = re.search(var + "=([a-zA-Z0-9]*)\n", out)
        if m:
            return m.group(1)
    return None


def get_current_side():
    """
    Return the current bootside
    """
    return get_uboot_env_value(CMD_BOOTSIDE)


def set_env(var, value):
    """
    Set a u-boot environment variable
    """
    return run_proc([CMD_FW_SETENV, var, value])[0]


def data_migration():
    """
    Handler to migrate data between two sides
    """
    syslog("igupd: data_migration: Starting")
    if run_proc([CMD_MIGRATE_DATA], timeout=40)[0]:
        syslog("igupd: data_migration: Data migration failed")
        return False
    else:
        syslog("igupd: data_migration: Data migration Completed")
        return True


def generate_md5sum(partition):
    """
    Handler to generate md5sum for each components
    """
    hash_md5 = hashlib.md5()
    with open(partition, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def reboot():
    """
    Call the 'reboot' command
    """
    return run_proc([CMD_REBOOT], 300)[0]
