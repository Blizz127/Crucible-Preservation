"""Process-lifetime notice shared by one configured QA shard and its backend.

The file contains no data. An exclusive flock means the guide is serving.
Retain the inode across restarts: unlinking it can split the lock from readers.
"""
import os
import stat

try:
    import fcntl
except ImportError:
    fcntl = None


def _open_marker(create):
    path = os.environ.get('CRUCIBLE_GUIDE_NOTICE_FILE', '')
    if not fcntl or not os.path.isabs(path):
        return None
    parent, name = os.path.split(path)
    try:
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parent_info = os.fstat(directory)
            if parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o077:
                return None
            access = os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
            flags = access | os.O_NOFOLLOW | os.O_NONBLOCK
            fd = os.open(name, flags, 0o600, dir_fd=directory)
        finally:
            os.close(directory)
    except (AttributeError, OSError):
        return None

    try:
        info = os.fstat(fd)
        if (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and not info.st_mode & 0o077 and info.st_nlink == 1
                and info.st_size == 0):
            return fd
    except OSError:
        pass
    os.close(fd)
    return None


def acquire():
    """Return (held descriptor, retry). Only a conflicting lock merits retry."""
    fd = _open_marker(create=True)
    if fd is None:
        return None, False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd, False
    except BlockingIOError:
        # A read may already hold the shared lock as the shard starts.
        retry = True
    except OSError:
        retry = False
    os.close(fd)
    return None, retry


def guide_is_serving():
    """Probe for the exclusive lock on an existing marker without changing it."""
    fd = _open_marker(create=False)
    if fd is None:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        pass
    finally:
        os.close(fd)
    return False
