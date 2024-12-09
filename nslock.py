import traceback
from copy import deepcopy
from threading import Lock
from typing import Dict, Union

_namespaceLock = Lock()
_namespace: Dict[str, Lock] = {}
_lockCounter: Dict[str, int] = {}


def getListOfActiveLocks(return_str: bool = False, sorted_by_refcount: bool = False) -> Union[Dict[str, int], str]:
    locks = {}
    with _namespaceLock:
        if not sorted_by_refcount:
            locks = deepcopy(_lockCounter)
        else:
            locks = {
                ns: refcount for ns, refcount in reversed(sorted(_lockCounter.items(), key=lambda item: item[1]))
            }

    if return_str:
        ret_str = "[\n  "
        ret_str += "\n  ".join([f"{k} (refcount {v})" for k, v in locks.items()])
        ret_str += "\n]"
        return ret_str

    return locks


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
