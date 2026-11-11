"""Настройки программы: %APPDATA%\\PlateReader\\settings.json."""
import configparser
import copy
import json
import os

from . import paths

DEFAULTS = {
    "camera": {"ip": "192.168.1.64", "port": 554, "user": "admin", "password": "",
               "channel": "101", "rtsp_url": ""},
    "recognition": {"quality": "accurate", "only_ru": True, "roi": None,
                    "min_confidence": 0.55, "accept_single_confidence": 0.85,
                    "repeat_timeout_sec": 120, "car_gap_sec": 1.5, "max_car_sec": 8,
                    "debug": False},
    "output": {"excel_file": "", "save_snapshots": True, "snapshot_dir": ""},
    "system": {"autostart": True, "start_minimized": False, "tray_on_close": True,
               "notify": False, "autorun_recognition": True},
}


def _merge(base, extra):
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def _fill_paths(s):
    journal = paths.documents() / "Проезды"
    if not s["output"]["excel_file"]:
        s["output"]["excel_file"] = str(journal / "проезды.xlsx")
    if not s["output"]["snapshot_dir"]:
        s["output"]["snapshot_dir"] = str(journal / "снимки")
    return s


def _from_old_script():
    """Перенос настроек из старой версии (скрипт с config.ini)."""
    ini = os.path.join(os.environ.get("LOCALAPPDATA", ""), "PlateReader", "config.ini")
    if not os.path.exists(ini):
        return None
    try:
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(ini, encoding="utf-8-sig")
        s = copy.deepcopy(DEFAULTS)
        c = cp["camera"]
        s["camera"].update(ip=c.get("ip", ""), port=int(c.get("port", "554") or 554),
                           user=c.get("user", "admin"), password=c.get("password", ""),
                           channel=c.get("channel", "101") or "101",
                           rtsp_url=c.get("rtsp_url", ""))
        if cp.has_section("recognition"):
            roi = cp["recognition"].get("roi", "").strip()
            if roi:
                s["recognition"]["roi"] = [int(x) for x in roi.split(",")]
        if cp.has_section("output"):
            s["output"]["excel_file"] = cp["output"].get("excel_file", "")
            snap = cp["output"].get("snapshot_dir", "").strip()
            s["output"]["save_snapshots"] = bool(snap)
            s["output"]["snapshot_dir"] = snap
        return s
    except Exception:
        return None


def load():
    f = paths.settings_file()
    s = copy.deepcopy(DEFAULTS)
    if f.exists():
        try:
            _merge(s, json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
        return _fill_paths(s), False
    old = _from_old_script()
    if old:
        _fill_paths(old)
        save(old)
        return old, False
    return _fill_paths(s), True   # True — первый запуск


def save(s):
    f = paths.settings_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


def set_autostart(enabled, exe_path):
    if os.name != "nt":
        return
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, "PlateReader", 0, winreg.REG_SZ, f'"{exe_path}" --minimized')
        else:
            try:
                winreg.DeleteValue(k, "PlateReader")
            except FileNotFoundError:
                pass
