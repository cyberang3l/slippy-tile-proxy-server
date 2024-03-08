import errno
import fcntl
import os
import sys
import time
import traceback
from threading import Lock
from typing import Optional

BOLD = '\033[1m'
YELLOW = '\033[93m'
ENDC = '\033[0m'


class FileLock:
    def __init__(self, filename: str, warnAfterSec: int = -1):
        self._filename = filename
        self._warnAfterSec = warnAfterSec
        self._fd: Optional[int] = None
        self._threadLock = Lock()

    def _closeFile(self):
        if self._fd:
            os.close(self._fd)
            self._fd = None

    def acquire(
            self,
            blocking: bool = True,
            timeout: int = -1) -> bool:
        with self._threadLock:
            if self._fd is not None:
                return True

            tStart = time.time()
            while True:
                if self._warnAfterSec != -1 and time.time() - tStart > self._warnAfterSec:
                    # An indication that a lock file was created manually or was not cleaned up
                    # from a previous session?
                    print(
                        f"{BOLD}{YELLOW}WARNING: Have been waiting to acquire the lock {self._filename} for more than {self._warnAfterSec} seconds{ENDC}",
                        file=sys.stderr)
                    time.sleep(5)

                try:
                    # To prevent race conditions when creating the file
                    # from concurrent threads, use the O_EXCL with O_CREAT
                    # flags. This will raise a FileExistsError if the file
                    # already exists that we must capture and retry to acquire
                    # the lock if the blocking is True and we are within the
                    # timeout range.
                    self._fd = os.open(
                        self._filename,
                        os.O_CREAT | os.O_RDWR | os.O_EXCL)
                except FileExistsError:
                    if blocking and (
                            timeout == -1 or timeout <= time.time() - tStart):
                        continue

                try:
                    fcntl.lockf(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as ex:
                    # Lock could not be acquired - it's already locked by another
                    # process
                    if ex.errno != errno.EAGAIN or ex.errno != errno.EACCES:
                        if blocking and (
                                timeout == -1 or timeout <= time.time() - tStart):
                            continue
                        self._closeFile()
                        return False
                    raise

                st0 = os.fstat(self._fd)
                try:
                    st1 = os.stat(self._filename)
                    if st0.st_ino == st1.st_ino:
                        # Lock acquired and is valid - the opened file descriptor's
                        # inode is the same as the file descriptor return by stat
                        # that queried the filename directly
                        return True
                except FileNotFoundError:
                    pass

                if blocking and (
                        timeout == -1 or (
                            timeout <= time.time() - tStart)):
                    continue
                self._closeFile()
                return False

    def release(self):
        with self._threadLock:
            if self._fd:
                os.unlink(self._filename)
                fcntl.lockf(self._fd, fcntl.LOCK_UN)
                self._closeFile()

    def __enter__(self):
        self.acquire(blocking=True)

    def __exit__(self, exc_type, exc_value, tb):
        self.release()
        if exc_type is not None:
            traceback.print_exception(exc_type, exc_value, tb)
            return False
        return True
