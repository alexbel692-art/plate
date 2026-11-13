"""Ядро: камера, распознавание номеров, голосование по машине, журнал Excel."""

import os
import re
import threading
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import quote

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Font  # noqa: E402

from . import paths  # noqa: E402
from .log import log  # noqa: E402

# ---------------------------------------------------------------- номера РФ
PLATE_LETTERS = set("ABEKMHOPCTYX")
LAT2CYR = str.maketrans("ABEKMHOPCTYX", "АВЕКМНОРСТУХ")
TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "U": "0", "B": "8", "I": "1", "L": "1",
            "Z": "2", "S": "5", "G": "6", "T": "7", "A": "4", "J": "1"}
TO_LETTER = {"0": "O", "8": "B", "4": "A", "7": "T", "6": "B", "D": "O", "Q": "O",
             "U": "Y", "V": "Y", "W": "M", "N": "H", "R": "P", "F": "E", "G": "C"}
TEMPLATE = "LDDDLLDD"
RU_PLATE = re.compile(r"^[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}$")

MODELS = {
    "accurate": ("yolo-v9-t-640-license-plate-end2end", "cct-s-v2-global-model"),
    "fast": ("yolo-v9-t-384-license-plate-end2end", "cct-xs-v2-global-model"),
}


def fix_ru(raw):
    t = re.sub(r"[^A-Z0-9]", "", raw.upper())
    if len(t) not in (8, 9):
        return None
    out = []
    for i, ch in enumerate(t):
        kind = TEMPLATE[i] if i < 8 else "D"
        if kind == "D":
            ch = ch if ch.isdigit() else TO_DIGIT.get(ch, ch)
            if not ch.isdigit():
                return None
        else:
            ch = ch if ch in PLATE_LETTERS else TO_LETTER.get(ch, ch)
            if ch not in PLATE_LETTERS:
                return None
        out.append(ch)
    lat = "".join(out)
    return lat, lat.translate(LAT2CYR)


def plain(raw):
    t = re.sub(r"[^A-Z0-9]", "", raw.upper())
    return (t, t.translate(LAT2CYR)) if len(t) >= 4 else None


def vote(reads):
    good = [r for r in reads if r["lat"]]
    if not good:
        return None
    length = Counter(len(r["lat"]) for r in good).most_common(1)[0][0]
    same = [r for r in good if len(r["lat"]) == length]
    out = []
    for i in range(length):
        score = defaultdict(float)
        for r in same:
            p = r["chars"][i] if i < len(r["chars"]) else r["conf"]
            score[r["lat"][i]] += p
        out.append(max(score, key=score.get))
    lat = "".join(out)
    best_conf = max(r["conf"] for r in same)
    agree = sum(1 for r in same if r["lat"] == lat)
    return lat.translate(LAT2CYR), best_conf, agree, len(same)


def save_jpg(path, img, q=85):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    if ok:
        buf.tofile(path)


def load_jpg(path):
    try:
        return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


def rtsp_url(cam):
    if cam.get("rtsp_url", "").strip():
        return cam["rtsp_url"].strip()
    return (f"rtsp://{quote(cam.get('user', 'admin'), safe='')}:"
            f"{quote(cam.get('password', ''), safe='')}"
            f"@{cam.get('ip', '')}:{cam.get('port', 554)}"
            f"/Streaming/Channels/{cam.get('channel', '101')}")


def cleanup(folder, days):
    limit = time.time() - days * 86400
    try:
        for e in os.scandir(folder):
            if e.is_file() and e.stat().st_mtime < limit:
                os.remove(e.path)
    except Exception:
        pass


# ---------------------------------------------------------------- модели
def use_bundled_models():
    """Модели лежат внутри программы — указываем библиотекам на эту папку."""
    base = paths.models_dir()
    if not base:
        return
    try:
        from open_image_models.detection.core import hub as oim_hub
        oim_hub.MODEL_CACHE_DIR = base / "open-image-models"
    except Exception:
        pass
    try:
        from fast_plate_ocr.inference import hub as fpo_hub
        fpo_hub.MODEL_CACHE_DIR = base / "fast-plate-ocr"
    except Exception:
        pass


class Recognizer:
    def __init__(self, quality="accurate", only_ru=True, roi=None):
        use_bundled_models()
        from fast_alpr import ALPR
        det, ocr = MODELS.get(quality, MODELS["accurate"])
        self.only_ru = only_ru
        self.roi = roi
        self.alpr = ALPR(detector_model=det, detector_conf_thresh=0.3, ocr_model=ocr)

    def _ocr(self, img):
        r = self.alpr.ocr.predict(img)
        if r is None or not r.text:
            return None
        chars = r.confidence if isinstance(r.confidence, list) else [float(r.confidence)]
        raw = re.sub(r"[^A-Z0-9]", "", r.text.upper())
        if not raw:
            return None
        if len(chars) > len(raw):
            chars = chars[:len(raw)]
        conf = float(sum(chars) / len(chars)) if chars else 0.0
        return raw, chars, conf

    def read_crop(self, crop):
        """Читает номер целиком и по частям (основная часть + регион отдельно,
        с несколькими вариантами границы). По частям нужно для 3-значных регионов
        (750, 790, 797…): целиком модель часто обрезает регион до двух цифр.
        Из вариантов, подходящих под формат РФ, берётся самый уверенный."""
        cands = []
        whole = self._ocr(crop)
        if whole:
            cands.append(whole)
        if self.only_ru:
            h, w = crop.shape[:2]
            mains = [self._ocr(crop[:, :int(w * k)]) for k in (0.74, 0.79, 0.84)]
            regs = [self._ocr(crop[:int(h * 0.80), int(w * k):]) for k in (0.68, 0.73, 0.78)]
            mains = [m for m in mains if m and len(m[0]) == 6]
            for m in mains:
                for g in regs:
                    if not g:
                        continue
                    digits = [(c, p) for c, p in zip(g[0], g[1]) if c.isdigit()][:3]
                    if len(digits) >= 2:
                        chars = list(m[1][:6]) + [p for _, p in digits]
                        cands.append((m[0] + "".join(c for c, _ in digits), chars,
                                      float(sum(chars) / len(chars))))
        best = None
        for raw, chars, conf in cands:
            fixed = fix_ru(raw) if self.only_ru else plain(raw)
            key = (fixed is not None, conf)
            if best is None or key > best[0]:
                best = (key, (raw, chars, conf, fixed))
        return best[1] if best else None

    def read(self, frame):
        ox = oy = 0
        work = frame
        if self.roi:
            x1, y1, x2, y2 = self.roi
            h, w = frame.shape[:2]
            x1, x2 = max(0, min(x1, w - 1)), max(1, min(x2, w))
            y1, y2 = max(0, min(y1, h - 1)), max(1, min(y2, h))
            if x2 - x1 > 20 and y2 - y1 > 20:
                work = frame[y1:y2, x1:x2]
                ox, oy = x1, y1
        H, W = work.shape[:2]
        reads = []
        for det in self.alpr.detector.predict(work):
            b = det.bounding_box
            bw, bh = b.x2 - b.x1, b.y2 - b.y1
            if bw < 20 or bh < 6:
                continue
            px, py = int(bw * 0.06), int(bh * 0.12)
            x1, y1 = max(b.x1 - px, 0), max(b.y1 - py, 0)
            x2, y2 = min(b.x2 + px, W), min(b.y2 + py, H)
            crop = work[y1:y2, x1:x2]
            best = self.read_crop(crop)
            if best is None:
                continue
            raw, chars, conf, fixed = best
            reads.append({
                "raw": raw, "lat": fixed[0] if fixed else None,
                "text": fixed[1] if fixed else None, "conf": conf, "chars": chars,
                "box": (x1 + ox, y1 + oy, x2 + ox, y2 + oy), "w": bw, "crop": crop,
            })
        return reads


# ---------------------------------------------------------------- движение
class MotionDetector:
    """Доля движущихся пикселей в области распознавания (для машин без номера)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.bg = cv2.createBackgroundSubtractorMOG2(history=150, varThreshold=32,
                                                     detectShadows=True)
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.n = 0

    def update(self, frame, roi=None):
        work = frame
        if roi:
            x1, y1, x2, y2 = roi
            if x2 - x1 > 20 and y2 - y1 > 20:
                work = frame[y1:y2, x1:x2]
        h, w = work.shape[:2]
        s = 320.0 / max(w, 1)
        small = cv2.resize(work, (320, max(1, int(h * s))), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (5, 5), 0)   # цветное: тёмная машина на тёмном асфальте
        fg = self.bg.apply(small)
        self.n += 1
        if self.n < 15:          # фон ещё не выучен
            return 0.0
        fg = (fg > 200).astype(np.uint8)       # без теней
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, self.kernel)
        return float(fg.mean())


# ---------------------------------------------------------------- камера
class Camera:
    def __init__(self, url):
        self.url = url
        self.frame = None
        self.seq = 0
        self.ts = 0.0
        self.connected = False
        self.error = ""
        self._stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    @staticmethod
    def open(url, timeout_ms=7000):
        params = []
        for name in ("CAP_PROP_OPEN_TIMEOUT_MSEC", "CAP_PROP_READ_TIMEOUT_MSEC"):
            if hasattr(cv2, name):
                params += [getattr(cv2, name), timeout_ms]
        try:
            return cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)
        except TypeError:
            return cv2.VideoCapture(url, cv2.CAP_FFMPEG)

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            cap = self.open(self.url)
            if not cap.isOpened():
                self.connected = False
                self.error = "нет связи с камерой"
                log("Нет связи с камерой, повтор через 5 с")
                self._stop.wait(5)
                continue
            self.connected = True
            self.error = ""
            log("Камера подключена")
            fails = 0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    fails += 1
                    if fails > 25:
                        log("Поток прервался, переподключаюсь")
                        break
                    continue
                fails = 0
                with self.lock:
                    self.frame = frame
                    self.seq += 1
                    self.ts = time.time()
            cap.release()
            self.connected = False
            self._stop.wait(2)

    def get(self):
        with self.lock:
            return self.seq, self.ts, self.frame


def grab_frame(url, tries=2):
    for _ in range(tries):
        cap = Camera.open(url, 6000)
        frame = None
        if cap.isOpened():
            for i in range(30):
                ok, f = cap.read()
                if ok:
                    frame = f
                    if i > 5:
                        break
        cap.release()
        if frame is not None:
            return frame
        time.sleep(1)
    return None


# ---------------------------------------------------------------- Excel
class ExcelLog:
    HEADER = ["Дата", "Время", "Номер", "Уверенность", "Прочтений", "Снимок", "Примечание"]

    def __init__(self, path):
        self.path = path
        self.pending = []
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if not os.path.exists(path):
            wb = Workbook()
            ws = wb.active
            ws.title = "Проезды"
            ws.append(self.HEADER)
            for c in ws[1]:
                c.font = Font(bold=True)
            for col, w in zip("ABCDEFG", (12, 10, 14, 13, 11, 60, 40)):
                ws.column_dimensions[col].width = w
            ws.freeze_panes = "A2"
            wb.save(path)

    def add(self, row):
        with self.lock:
            self.pending.append(row)
        self.flush()

    def flush(self):
        with self.lock:
            if not self.pending:
                return True
            try:
                wb = load_workbook(self.path)
                ws = wb.active
                if ws.cell(1, 5).value == "Снимок":
                    ws.insert_cols(5)
                    ws.cell(1, 5, "Прочтений").font = Font(bold=True)
                if ws.cell(1, 7).value != "Примечание":
                    ws.cell(1, 7, "Примечание").font = Font(bold=True)
                    ws.column_dimensions["G"].width = 40
                for r in self.pending:
                    ws.append(r)
                ws.auto_filter.ref = f"A1:G{ws.max_row}"
                wb.save(self.path)
                self.pending.clear()
                return True
            except PermissionError:
                return False
            except Exception as e:
                log(f"Ошибка записи в Excel: {e}")
                return False

    def last_rows(self, n=200):
        try:
            wb = load_workbook(self.path, read_only=True)
            ws = wb.active
            rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r and r[2]]
            wb.close()
            out = []
            for r in rows[-n:]:
                r = list(r) + [None] * 7
                if isinstance(r[4], str) and (":" in r[4] or "\\" in r[4]) and not r[5]:
                    r[5], r[4] = r[4], ""
                out.append({"date": str(r[0] or ""), "time": str(r[1] or ""),
                            "plate": str(r[2] or ""), "conf": r[3], "agree": r[4] or "",
                            "snap": r[5] or "", "note": r[6] or ""})
            return out
        except Exception:
            return []


# ---------------------------------------------------------------- движок
class Engine:
    """Работает в своих потоках, сообщает о событиях через колбэки."""

    def __init__(self, settings, on_pass=None, on_status=None):
        self.s = settings
        self.on_pass = on_pass or (lambda d: None)
        self.on_status = on_status or (lambda text, ok: None)
        self.cam = None
        self.rec = None
        self.xl = None
        self.running = False
        self.thread = None
        self._stop = threading.Event()
        self.boxes = []          # [(x1,y1,x2,y2,text,ts)] для подсветки на видео
        self.fps = 0.0
        self.passes_today = 0
        self.alive_ts = 0.0

    # -- управление
    def start(self):
        if self.running:
            return
        self._stop.clear()
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        if self.cam:
            self.cam.stop()
        if self.thread:
            self.thread.join(timeout=5)
        self.running = False
        self.cam = None

    def frame(self):
        if not self.cam:
            return None
        return self.cam.get()[2]

    # -- основной цикл
    def _run(self):
        s = self.s
        rec_s, out_s = s["recognition"], s["output"]
        try:
            self.on_status("Загрузка моделей распознавания…", True)
            self.rec = Recognizer(rec_s.get("quality", "accurate"),
                                  rec_s.get("only_ru", True), rec_s.get("roi"))
            self.xl = ExcelLog(out_s["excel_file"])
        except Exception:
            log("Ошибка запуска:\n" + traceback.format_exc())
            self.on_status("Ошибка загрузки моделей — см. журнал программы", False)
            self.running = False
            return

        self.cam = Camera(rtsp_url(s["camera"]))
        snap_dir = out_s.get("snapshot_dir", "") if out_s.get("save_snapshots", True) else ""
        debug_dir = paths.debug_dir() if rec_s.get("debug") else ""
        for d in (snap_dir, debug_dir):
            if d:
                os.makedirs(d, exist_ok=True)

        min_conf = rec_s.get("min_confidence", 0.55)
        single = rec_s.get("accept_single_confidence", 0.85)
        gap, max_car = rec_s.get("car_gap_sec", 1.5), rec_s.get("max_car_sec", 8)
        repeat = rec_s.get("repeat_timeout_sec", 120)
        rec_unrec = rec_s.get("record_unrecognized", True)
        use_motion = rec_s.get("motion_detect", True)
        motion_thr = rec_s.get("motion_percent", 8) / 100.0
        unrec_gap = rec_s.get("unrecognized_gap_sec", 20)
        motion = MotionDetector() if (rec_unrec and use_motion) else None
        mv = {"active": False, "start": 0.0, "last": 0.0, "peak": 0.0, "frame": None}
        marks = {"plate_seen": 0.0, "unrec": 0.0}

        last_seen, car, best = {}, [], {"w": 0, "frame": None}
        car_start = car_last = 0.0
        last_seq, frames, tick = -1, 0, time.time()
        last_flush = 0.0
        log(f"Распознавание запущено, область {rec_s.get('roi') or 'весь кадр'}")

        def record_unrecognized(frame, note, since=None):
            if not rec_unrec:
                return
            # по номеру без движения (табличка, стоящая машина) — не пишем
            if since is not None and motion is not None and mv["last"] < since - 1:
                log(f"  без движения в кадре — не записываю ({note})")
                return
            now_t = time.time()
            if now_t - marks["unrec"] < unrec_gap:
                return
            marks["unrec"] = now_t
            now = datetime.now()
            snap = ""
            if snap_dir and frame is not None:
                snap = os.path.join(snap_dir, f"{now:%Y%m%d_%H%M%S}_не_распознан.jpg")
                save_jpg(snap, frame)
            self.xl.add([now.strftime("%d.%m.%Y"), now.strftime("%H:%M:%S"), "не распознан",
                         None, None, snap, note])
            st["saved"] += 1
            log(f"ЗАПИСАН без номера: {note}")
            self.on_pass({"date": now.strftime("%d.%m.%Y"), "time": now.strftime("%H:%M:%S"),
                          "plate": "не распознан", "conf": None, "agree": "",
                          "snap": snap, "note": note})

        def finish_car():
            nonlocal car
            reads, frame = car, best["frame"]
            car = []
            best["w"], best["frame"] = 0, None
            res = vote([r for r in reads if r["conf"] >= min_conf])
            if not res:
                guess = vote(reads)
                log(f"машина: номер не прочитан ({len(reads)} попыток: "
                    f"{', '.join(sorted({r['raw'] for r in reads}))[:100]})")
                record_unrecognized(frame, "номер не прочитан"
                                    + (f", похоже на {guess[0]}" if guess else ""), car_start)
                return
            plate, conf, agree, n = res
            if not (agree >= 2 or conf >= single) or (
                    rec_s.get("only_ru", True) and not RU_PLATE.match(plate)):
                log(f"машина: {plate} — не уверен ({conf:.0%}, совпадений {agree}/{n})")
                record_unrecognized(frame, f"не уверен, похоже на {plate} ({conf:.0%})", car_start)
                return
            now = datetime.now()
            prev = last_seen.get(plate)
            if prev and (now - prev).total_seconds() < repeat:
                log(f"{plate} — повтор, не записываю")
                return
            last_seen[plate] = now
            snap = ""
            if snap_dir and frame is not None:
                snap = os.path.join(snap_dir, f"{now:%Y%m%d_%H%M%S}_{plate}.jpg")
                save_jpg(snap, frame)
            self.xl.add([now.strftime("%d.%m.%Y"), now.strftime("%H:%M:%S"), plate,
                         round(conf, 2), f"{agree}/{n}", snap, ""])
            self.passes_today += 1
            st["saved"] += 1
            log(f"ЗАПИСАН {plate} ({conf:.0%}, совпадений {agree}/{n})")
            self.on_pass({"date": now.strftime("%d.%m.%Y"), "time": now.strftime("%H:%M:%S"),
                          "plate": plate, "conf": round(conf, 2), "agree": f"{agree}/{n}",
                          "snap": snap})

        status_ok = None
        st = {"frames": 0, "plates": 0, "saved": 0, "beat": time.time(), "err_ts": 0.0}

        def step():
            nonlocal status_ok, last_seq, frames, tick, last_flush, car_start, car_last, car
            now = time.time()
            self.alive_ts = now
            if now - st["beat"] >= 600:
                cam_age = now - self.cam.ts if self.cam.ts else -1
                log(f"работаю: за 10 мин кадров {st['frames']}, кадров с номером {st['plates']}, "
                    f"записано {st['saved']}; камера: "
                    + ("ок" if 0 <= cam_age < 10 else f"нет кадров {cam_age:.0f} с"))
                st.update(frames=0, plates=0, saved=0, beat=now)
            if self.xl.pending and now - last_flush > 5:
                last_flush = now
                self.xl.flush()
            if car and (now - car_last > gap or now - car_start > max_car):
                finish_car()
            if now - tick >= 5:
                self.fps = frames / (now - tick)
                frames, tick = 0, now
                if debug_dir:
                    cleanup(debug_dir, 2)
            seq, ts, frame = self.cam.get()
            ok = bool(self.cam.connected and ts and now - ts < 10)
            if ok != status_ok:
                status_ok = ok
                self.on_status("Камера подключена, идёт распознавание" if ok
                               else "Нет изображения с камеры — переподключаюсь…", bool(ok))
            if frame is None or seq == last_seq:
                return
            last_seq = seq
            frames += 1
            st["frames"] += 1
            if motion is not None:
                frac = motion.update(frame, rec_s.get("roi"))
                if frac > motion_thr:
                    if not mv["active"]:
                        mv.update(active=True, start=now, peak=0.0, frame=None)
                    mv["last"] = now
                    if frac > mv["peak"]:
                        mv["peak"], mv["frame"] = frac, frame.copy()
                elif mv["active"] and now - mv["last"] > 2.0:
                    mv["active"] = False
                    dur = mv["last"] - mv["start"]
                    plate_near = marks["plate_seen"] >= mv["start"] - 2
                    if dur > 40:
                        motion.reset()          # смена освещения / стоящая машина
                    elif dur >= 0.8 and not plate_near and not car:
                        record_unrecognized(mv["frame"], "номер не найден в кадре "
                                            "(машина по движению)")
                    mv["frame"] = None
            try:
                reads = self.rec.read(frame)
            except Exception as e:
                log(f"Ошибка распознавания: {e}")
                return
            self.boxes = [(*r["box"], r["text"] or r["raw"], now) for r in reads]
            if not reads:
                return
            st["plates"] += 1
            if debug_dir:
                for r in reads:
                    name = f"{datetime.now():%Y%m%d_%H%M%S_%f}"[:-3] + \
                           f"_{r['lat'] or 'X' + r['raw']}_{int(r['conf'] * 100)}.jpg"
                    save_jpg(os.path.join(debug_dir, name), r["crop"], 95)
            marks["plate_seen"] = now
            if not car:
                car_start = now
            car_last = now
            car.extend(reads)
            w = max(r["w"] for r in reads)
            if w >= best["w"]:
                best["w"], best["frame"] = w, frame.copy()
                for r in reads:
                    x1, y1, x2, y2 = r["box"]
                    cv2.rectangle(best["frame"], (x1, y1), (x2, y2), (0, 255, 0), 2)

        while not self._stop.is_set():
            self._stop.wait(0.05)
            try:
                step()
            except Exception:
                car = []
                best["w"], best["frame"] = 0, None
                if time.time() - st["err_ts"] > 60:
                    st["err_ts"] = time.time()
                    log("Ошибка в цикле распознавания (продолжаю работу):\n"
                        + traceback.format_exc())
        if car:
            finish_car()
        if self.xl:
            self.xl.flush()
        log("Распознавание остановлено")
