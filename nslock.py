import traceback
from threading import Lock
from typing import Dict

_namespaceLock = Lock()
_namespace: Dict[str, Lock] = {}
_lockCounter: Dict[str, int] = {}


class NamespaceLock:
    def __init__(self, namespace: str):
        self._namespace = namespace
        with _namespaceLock:
            if self._namespace not in _namespace.keys():
                _namespace[self._namespace] = Lock()
                _lockCounter[self._namespace] = 1
            else:
                _lockCounter[self._namespace] += 1

    def __enter__(self):
        _namespace[self._namespace].acquire()

    def __exit__(self, exc_type, exc_value, tb):
        with _namespaceLock:
            lock = _namespace[self._namespace]
            _lockCounter[self._namespace] -= 1
            if _lockCounter[self._namespace] == 0:
                lock = _namespace.pop(self._namespace)
                del _lockCounter[self._namespace]
            lock.release()

        if exc_type is not None:
            traceback.print_exception(exc_type, exc_value, tb)
            return False

        return True
