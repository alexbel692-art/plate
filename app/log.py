import threading
from datetime import datetime

from . import paths

_lock = threading.Lock()
listeners = []


def log(msg):
    line = f"{datetime.now():%d.%m.%Y %H:%M:%S}  {msg}"
    with _lock:
        try:
            f = paths.log_file()
            if f.exists() and f.stat().st_size > 5_000_000:
                f.replace(f.with_suffix(".old.txt"))
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
    for cb in list(listeners):
        try:
            cb(line)
        except Exception:
            pass
