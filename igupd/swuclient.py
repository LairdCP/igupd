import select
import threading
import subprocess
from syslog import LOG_ERR, syslog
import swclient

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

SWUPDATE_POLL_TIMEOUT = 0.2


class SWUpdateClient:
    def __init__(self, handler):
        self.recv_handler = handler
        self.proc = None
        self.update_in_progress = False
        self.state = None
        self.msg_fd = -1

    def initiate_swupdate(self, cmd):
        self.update_in_progress = True
        try:
            self.msg_fd = swclient.open_progress_ipc()
            if self.msg_fd < 0:
                syslog(
                    LOG_ERR, "initiate_swupdate: error opening progress IPC connection"
                )
                return

            progress_thread = threading.Thread(
                target=self.monitor_update_progress, daemon=True
            )
            self.proc = subprocess.Popen(cmd, shell=False)

            progress_thread.start()
            (out, err) = self.proc.communicate()

            if self.proc.returncode != 0:
                if self.proc.returncode == SIGNAL_TERM:
                    syslog(
                        "Subprocess was terminated by SIGTERM %d"
                        % (self.proc.returncode)
                    )
                elif self.proc.returncode == SIGNAL_KILL:
                    syslog(
                        "Subprocess was terminated by SIGKILL %d"
                        % (self.proc.returncode)
                    )
                else:
                    syslog(
                        "command failed stopping, exit-code=%d" % (self.proc.returncode)
                    )
                    self.progress_handler(
                        SWU_STATUS_BAD_CMD, None, self.proc.returncode
                    )

            if self.proc.poll() is None:
                self.proc.kill()

            self.update_in_progress = False
            progress_thread.join()

            swclient.close_progress_ipc(self.msg_fd)
            self.msg_fd = -1
        except Exception as e:
            syslog(LOG_ERR, "initiate_swupdate: error performing update: %s" % str(e))

    def monitor_update_progress(self):
        try:
            poller = select.poll()
            poller.register(self.msg_fd, select.POLLIN)

            # Only monitor the progress socket while an update is in progress
            while self.update_in_progress:
                events = poller.poll(SWUPDATE_POLL_TIMEOUT)
                for descriptor, event in events:
                    if descriptor == self.msg_fd and event is select.POLLIN:
                        # Read data
                        fw_update_state = swclient.read_progress_ipc(self.msg_fd)
                        if fw_update_state == None:
                            continue

                        self.progress_handler(
                            fw_update_state[0],  # status
                            fw_update_state[4],  # cur_image
                            fw_update_state[5],  # info
                        )

            # Cleanup
            poller.unregister(self.msg_fd)
        except Exception as e:
            syslog(LOG_ERR, "Failed reading progress update: '%s'" % str(e))

    def progress_handler(self, status, curr_image, msg):
        self.state = status
        if curr_image:
            rcurr_img = curr_image.strip("\x00")
        else:
            rcurr_img = None
        self.recv_handler(status, rcurr_img, msg)

    def get_state(self):
        return self.state
