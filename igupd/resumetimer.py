import time
import threading

MAX_PAUSE_TIME = 7200


class ResumableTimer:
    """
    Resumable timer will run the given callback function
    after the given timeout. The Timer can be paused more than once
    until the MAX_PAUSE_TIME has been reached
    """

    def __init__(self, timeout, callback):
        self.timeout = timeout
        self.callback = callback
        self.timer = threading.Timer(timeout, callback)
        self.start_time = time.time()
        self.pause_time = 0
        self.pause_timer = None

    def start(self):
        self.timer.start()

    def pause(self, pause_seconds):
        """
        Stop the main timer and start the pause timer.  Once the
        pause timer has run to completion the main timer will
        proceed with rest of its timeout.
        """
        cur_time = time.time()

        if self.pause_timer is None:
            self.pause_time = cur_time
            self.timer.cancel()
        else:
            elapsed = cur_time - self.pause_time
            if elapsed + pause_seconds > MAX_PAUSE_TIME:
                ret = -2
            elif pause_seconds == 0:
                ret = 0
                self.pause_timer.cancel()
                self.resume()
            else:
                ret = -1
            return ret

        self.pause_timer = threading.Timer(pause_seconds, self.resume)
        self.pause_timer.start()
        return 0

    def resume(self):
        """
        Resume the main timer and schedule the callback function
        to be executed
        """
        self.timer = threading.Timer(
            self.timeout - (self.pause_time - self.start_time), self.callback
        )

        self.timer.start()
