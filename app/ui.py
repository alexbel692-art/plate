"""Окна программы: главное, настройки, область распознавания."""

import os
import sys
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox,
    QSplitter, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget,
)

from . import __version__, paths, settings as settings_mod
from .engine import Engine, grab_frame, load_jpg, rtsp_url
from .log import listeners, log

ACCENT = "#1f6feb"


def to_pixmap(img_bgr, max_w=None, max_h=None):
    if img_bgr is None:
        return QPixmap()
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    pm = QPixmap.fromImage(qimg)
    if max_w and max_h:
        pm = pm.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pm


def app_icon():
    ico = paths.resource("icon.ico")
    if ico.exists():
        return QIcon(str(ico))
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 14, 60, 36, 6, 6)
    p.setPen(QColor("white"))
    p.setFont(QFont("Arial", 16, QFont.Bold))
    p.drawText(QRect(2, 14, 60, 36), Qt.AlignCenter, "A77")
    p.end()
    return QIcon(pm)


class Bridge(QObject):
    passed = Signal(dict)
    status = Signal(str, bool)
    logline = Signal(str)
    check_done = Signal(object, str)


# ====================================================================== видео
class VideoView(QLabel):
    """Показывает кадр с камеры, область распознавания и найденные номера."""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background:#111; color:#aaa; border-radius:6px;")
        self.setText("Нет изображения")

    def show_frame(self, frame, roi=None, boxes=()):
        if frame is None:
            return
        img = frame.copy()
        h, w = img.shape[:2]
        thick = max(2, w // 640)
        if roi:
            x1, y1, x2, y2 = roi
            cv2.rectangle(img, (x1, y1), (x2, y2), (255, 200, 0), thick)
        for (x1, y1, x2, y2, text, _ts) in boxes:
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 230, 0), thick + 1)
            if text:
                cv2.putText(img, "".join(c if ord(c) < 128 else "?" for c in _lat(text)),
                            (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.6 * thick, (0, 230, 0), thick)
        self.setPixmap(to_pixmap(img, self.width(), self.height()))


_CYR2LAT = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")


def _lat(text):
    return text.translate(_CYR2LAT)


# ====================================================================== область
class RoiCanvas(QLabel):
    def __init__(self, frame, roi):
        super().__init__()
        self.frame = frame
        h, w = frame.shape[:2]
        self.img_w, self.img_h = w, h
        self.scale = min(1.0, 1200 / w, 700 / h)
        self.pm = to_pixmap(frame, int(w * self.scale), int(h * self.scale))
        self.setPixmap(self.pm)
        self.setFixedSize(self.pm.size())
        self.setCursor(Qt.CrossCursor)
        self.start = None
        self.rect = QRect()
        if roi:
            x1, y1, x2, y2 = roi
            self.rect = QRect(QPoint(int(x1 * self.scale), int(y1 * self.scale)),
                              QPoint(int(x2 * self.scale), int(y2 * self.scale)))

    def mousePressEvent(self, e):
        self.start = e.position().toPoint()
        self.rect = QRect(self.start, self.start)
        self.update()

    def mouseMoveEvent(self, e):
        if self.start is not None:
            self.rect = QRect(self.start, e.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, e):
        self.start = None

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.rect.isNull():
            return
        p = QPainter(self)
        shade = QColor(0, 0, 0, 120)
        full = self.pm.rect()
        r = self.rect.intersected(full)
        p.fillRect(QRect(0, 0, full.width(), r.top()), shade)
        p.fillRect(QRect(0, r.bottom(), full.width(), full.height() - r.bottom()), shade)
        p.fillRect(QRect(0, r.top(), r.left(), r.height()), shade)
        p.fillRect(QRect(r.right(), r.top(), full.width() - r.right(), r.height()), shade)
        p.setPen(QPen(QColor(255, 200, 0), 3))
        p.drawRect(r)
        p.end()

    def roi(self):
        r = self.rect.normalized()
        if r.width() < 20 or r.height() < 15:
            return None
        s = self.scale
        return [max(0, int(r.left() / s)), max(0, int(r.top() / s)),
                min(self.img_w, int(r.right() / s)), min(self.img_h, int(r.bottom() / s))]


class RoiDialog(QDialog):
    def __init__(self, frame, roi, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Область распознавания")
        self.result_roi = roi
        lay = QVBoxLayout(self)
        tip = QLabel("Обведите мышью зону, где проезжают номера машин (с небольшим запасом). "
                     "Номера ищутся только внутри неё — так быстрее и точнее.")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.canvas = RoiCanvas(frame, roi)
        lay.addWidget(self.canvas, alignment=Qt.AlignCenter)
        bar = QHBoxLayout()
        full = QPushButton("Весь кадр")
        full.clicked.connect(self._full)
        bar.addWidget(full)
        bar.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Сохранить")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        bar.addWidget(bb)
        lay.addLayout(bar)

    def _full(self):
        self.result_roi = None
        self.accept()

    def _save(self):
        r = self.canvas.roi()
        if r is None:
            QMessageBox.warning(self, "Область", "Обведите область мышью или нажмите «Весь кадр».")
            return
        self.result_roi = r
        self.accept()


# ====================================================================== настройки
class SettingsDialog(QDialog):
    def __init__(self, s, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(560)
        self.s = s
        self.bridge = Bridge()
        self.bridge.check_done.connect(self._check_done)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # --- камера
        cam = s["camera"]
        w = QWidget()
        f = QFormLayout(w)
        self.ip = QLineEdit(cam["ip"])
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(cam["port"]))
        self.user = QLineEdit(cam["user"])
        self.password = QLineEdit(cam["password"])
        self.password.setEchoMode(QLineEdit.Password)
        show = QCheckBox("показать")
        show.toggled.connect(lambda v: self.password.setEchoMode(
            QLineEdit.Normal if v else QLineEdit.Password))
        pw = QHBoxLayout()
        pw.addWidget(self.password)
        pw.addWidget(show)
        self.channel = QComboBox()
        self.channel.addItem("Основной поток (лучше для номеров)", "101")
        self.channel.addItem("Дополнительный поток", "102")
        self.channel.setCurrentIndex(0 if str(cam["channel"]) == "101" else 1)
        self.rtsp = QLineEdit(cam.get("rtsp_url", ""))
        self.rtsp.setPlaceholderText("необязательно: rtsp://… для камер других марок")
        self.check_btn = QPushButton("Проверить подключение")
        self.check_btn.clicked.connect(self._check)
        self.check_lbl = QLabel("")
        self.check_lbl.setWordWrap(True)
        f.addRow("IP-адрес камеры:", self.ip)
        f.addRow("RTSP-порт:", self.port)
        f.addRow("Логин:", self.user)
        f.addRow("Пароль:", pw)
        f.addRow("Видеопоток:", self.channel)
        f.addRow("Свой адрес потока:", self.rtsp)
        f.addRow(self.check_btn, self.check_lbl)
        tabs.addTab(w, "Камера")

        # --- распознавание
        rec = s["recognition"]
        w = QWidget()
        f = QFormLayout(w)
        self.quality = QComboBox()
        self.quality.addItem("Точное (рекомендуется)", "accurate")
        self.quality.addItem("Быстрое (для слабого компьютера)", "fast")
        self.quality.setCurrentIndex(0 if rec["quality"] == "accurate" else 1)
        self.only_ru = QCheckBox("Только номера РФ (А123ВС77) — отсеивает мусор")
        self.only_ru.setChecked(rec["only_ru"])
        self.min_conf = QSpinBox()
        self.min_conf.setRange(20, 99)
        self.min_conf.setSuffix(" %")
        self.min_conf.setValue(int(rec["min_confidence"] * 100))
        self.single = QSpinBox()
        self.single.setRange(50, 100)
        self.single.setSuffix(" %")
        self.single.setValue(int(rec["accept_single_confidence"] * 100))
        self.repeat = QSpinBox()
        self.repeat.setRange(0, 86400)
        self.repeat.setSuffix(" с")
        self.repeat.setValue(int(rec["repeat_timeout_sec"]))
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0.5, 10)
        self.gap.setSingleStep(0.5)
        self.gap.setSuffix(" с")
        self.gap.setValue(float(rec["car_gap_sec"]))
        self.debug = QCheckBox("Сохранять каждое прочтение в папку «отладка» (2 дня)")
        self.debug.setChecked(rec["debug"])
        self.unrec = QCheckBox("Записывать машины с нераспознанным номером (снимок + «не распознан»)")
        self.unrec.setChecked(rec.get("record_unrecognized", True))
        self.motion = QCheckBox("Замечать машину по движению, даже если номер не виден")
        self.motion.setChecked(rec.get("motion_detect", True))
        self.motion_pct = QSpinBox()
        self.motion_pct.setRange(1, 60)
        self.motion_pct.setSuffix(" % области")
        self.motion_pct.setValue(int(rec.get("motion_percent", 8)))
        self.motion_pct.setToolTip("Какая часть области распознавания должна двигаться, "
                                   "чтобы считать это машиной. Больше — меньше ложных записей.")
        self.unrec.toggled.connect(self.motion.setEnabled)
        self.motion.setEnabled(self.unrec.isChecked())
        f.addRow("Модель:", self.quality)
        f.addRow("", self.only_ru)
        f.addRow("Минимальная уверенность прочтения:", self.min_conf)
        f.addRow("Записывать по одному прочтению от:", self.single)
        f.addRow("Не повторять тот же номер в течение:", self.repeat)
        f.addRow("Машина уехала, если номера нет:", self.gap)
        f.addRow("", self.unrec)
        f.addRow("", self.motion)
        f.addRow("Машина — если движется не меньше:", self.motion_pct)
        f.addRow("", self.debug)
        roi_lbl = QLabel("Область распознавания выделяется в главном окне: "
                         "меню «Настройки» → «Область распознавания».")
        roi_lbl.setWordWrap(True)
        roi_lbl.setStyleSheet("color:#666")
        f.addRow(roi_lbl)
        tabs.addTab(w, "Распознавание")

        # --- журнал
        out = s["output"]
        w = QWidget()
        f = QFormLayout(w)
        self.excel = QLineEdit(out["excel_file"])
        b1 = QPushButton("…")
        b1.setFixedWidth(32)
        b1.clicked.connect(self._pick_excel)
        r1 = QHBoxLayout()
        r1.addWidget(self.excel)
        r1.addWidget(b1)
        self.save_snaps = QCheckBox("Сохранять снимок машины при каждом проезде")
        self.save_snaps.setChecked(out["save_snapshots"])
        self.snaps = QLineEdit(out["snapshot_dir"])
        b2 = QPushButton("…")
        b2.setFixedWidth(32)
        b2.clicked.connect(self._pick_snaps)
        r2 = QHBoxLayout()
        r2.addWidget(self.snaps)
        r2.addWidget(b2)
        f.addRow("Файл Excel:", r1)
        f.addRow("", self.save_snaps)
        f.addRow("Папка снимков:", r2)
        tabs.addTab(w, "Журнал")

        # --- система
        sy = s["system"]
        w = QWidget()
        f = QFormLayout(w)
        self.autostart = QCheckBox("Запускать вместе с Windows")
        self.autostart.setChecked(sy["autostart"])
        self.autorun = QCheckBox("Сразу начинать распознавание при запуске")
        self.autorun.setChecked(sy["autorun_recognition"])
        self.minimized = QCheckBox("Запускать свёрнутой в значок у часов")
        self.minimized.setChecked(sy["start_minimized"])
        self.tray = QCheckBox("Крестик сворачивает в значок у часов (программа продолжает работать)")
        self.tray.setChecked(sy["tray_on_close"])
        self.notify = QCheckBox("Всплывающее уведомление о каждом проезде")
        self.notify.setChecked(sy["notify"])
        for cb in (self.autostart, self.autorun, self.minimized, self.tray, self.notify):
            f.addRow(cb)
        tabs.addTab(w, "Система")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Сохранить")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _cam_values(self):
        return {"ip": self.ip.text().strip(), "port": self.port.value(),
                "user": self.user.text().strip(), "password": self.password.text(),
                "channel": self.channel.currentData(), "rtsp_url": self.rtsp.text().strip()}

    def _check(self):
        self.check_btn.setEnabled(False)
        self.check_lbl.setText("Подключаюсь…")
        url = rtsp_url(self._cam_values())

        def work():
            frame = grab_frame(url)
            self.bridge.check_done.emit(frame, "")
        threading.Thread(target=work, daemon=True).start()

    def _check_done(self, frame, _):
        self.check_btn.setEnabled(True)
        if frame is None:
            self.check_lbl.setText("<span style='color:#c00'>Не удалось получить видео. "
                                   "Проверьте IP, логин, пароль и что камера в сети.</span>")
        else:
            h, w = frame.shape[:2]
            self.check_lbl.setText(f"<span style='color:#080'>Камера отвечает, кадр {w}×{h}.</span>")

    def _pick_excel(self):
        p, _ = QFileDialog.getSaveFileName(self, "Файл журнала", self.excel.text(),
                                           "Excel (*.xlsx)", options=QFileDialog.DontConfirmOverwrite)
        if p:
            self.excel.setText(p if p.lower().endswith(".xlsx") else p + ".xlsx")

    def _pick_snaps(self):
        p = QFileDialog.getExistingDirectory(self, "Папка снимков", self.snaps.text())
        if p:
            self.snaps.setText(p)

    def values(self):
        s = {k: dict(v) for k, v in self.s.items()}
        s["camera"].update(self._cam_values())
        s["recognition"].update(
            quality=self.quality.currentData(), only_ru=self.only_ru.isChecked(),
            min_confidence=self.min_conf.value() / 100,
            accept_single_confidence=self.single.value() / 100,
            repeat_timeout_sec=self.repeat.value(), car_gap_sec=self.gap.value(),
            debug=self.debug.isChecked(), record_unrecognized=self.unrec.isChecked(),
            motion_detect=self.motion.isChecked(), motion_percent=self.motion_pct.value())
        s["output"].update(excel_file=self.excel.text().strip(),
                           save_snapshots=self.save_snaps.isChecked(),
                           snapshot_dir=self.snaps.text().strip())
        s["system"].update(autostart=self.autostart.isChecked(),
                           autorun_recognition=self.autorun.isChecked(),
                           start_minimized=self.minimized.isChecked(),
                           tray_on_close=self.tray.isChecked(), notify=self.notify.isChecked())
        return s


# ====================================================================== главное окно
class MainWindow(QMainWindow):
    def __init__(self, s, first_run=False):
        super().__init__()
        self.s = s
        self.quitting = False
        self.setWindowTitle(f"PlateReader — распознавание номеров  {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1280, 760)

        self.bridge = Bridge()
        self.bridge.passed.connect(self._on_pass)
        self.bridge.status.connect(self._on_status)
        self.bridge.logline.connect(self._on_log)
        listeners.append(self.bridge.logline.emit)
        self.engine = None

        # --- меню
        m = self.menuBar()
        fm = m.addMenu("Файл")
        self._act(fm, "Открыть журнал Excel", self.open_excel)
        self._act(fm, "Открыть папку снимков", self.open_snaps)
        fm.addSeparator()
        self._act(fm, "Выход", self.quit_app)
        sm = m.addMenu("Настройки")
        self._act(sm, "Настройки…", self.open_settings)
        self._act(sm, "Область распознавания…", self.open_roi)
        hm = m.addMenu("Справка")
        self._act(hm, "Журнал работы программы", self.open_log)
        self._act(hm, "Папка отладки", lambda: self._open_path(paths.debug_dir()))
        self._act(hm, "О программе", self.about)

        # --- центр
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        top = QHBoxLayout()
        self.start_btn = QPushButton("▶  Начать распознавание")
        self.start_btn.setMinimumHeight(38)
        self.start_btn.clicked.connect(self.toggle)
        self.state_lbl = QLabel("Остановлено")
        self.state_lbl.setStyleSheet("font-size:14px;")
        top.addWidget(self.start_btn)
        top.addSpacing(12)
        top.addWidget(self.state_lbl, 1)
        excel_btn = QPushButton("Открыть журнал Excel")
        excel_btn.clicked.connect(self.open_excel)
        roi_btn = QPushButton("Область распознавания")
        roi_btn.clicked.connect(self.open_roi)
        set_btn = QPushButton("Настройки")
        set_btn.clicked.connect(self.open_settings)
        for b in (roi_btn, excel_btn, set_btn):
            top.addWidget(b)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.video = VideoView()
        ll.addWidget(self.video, 3)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setFont(QFont("Consolas", 9))
        ll.addWidget(self.log_view, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.count_lbl = QLabel("Проездов сегодня: 0")
        self.count_lbl.setStyleSheet("font-size:15px; font-weight:bold;")
        rl.addWidget(self.count_lbl)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Дата", "Время", "Номер", "Увер.", "Примечание"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._show_snap)
        self.table.cellDoubleClicked.connect(self._open_snap)
        rl.addWidget(self.table, 3)
        self.snap = QLabel("Выберите проезд, чтобы увидеть снимок")
        self.snap.setAlignment(Qt.AlignCenter)
        self.snap.setMinimumHeight(220)
        self.snap.setStyleSheet("background:#f2f2f2; color:#888; border-radius:6px;")
        rl.addWidget(self.snap, 2)
        split.addWidget(right)
        split.setSizes([820, 440])
        root.addWidget(split, 1)

        self.statusBar().showMessage("Готово")

        # --- значок у часов
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("PlateReader — распознавание номеров")
        tm = QMenu()
        tm.addAction("Открыть", self.show_normal)
        tm.addAction("Открыть журнал Excel", self.open_excel)
        tm.addSeparator()
        tm.addAction("Выход", self.quit_app)
        self.tray.setContextMenu(tm)
        self.tray.activated.connect(
            lambda r: self.show_normal() if r in (QSystemTrayIcon.Trigger,
                                                  QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(150)

        self.watchdog = QTimer(self)
        self.watchdog.timeout.connect(self._watchdog)
        self.watchdog.start(30000)

        self._load_history()
        self._apply_autostart()
        if first_run:
            QTimer.singleShot(300, self._first_run)
        elif s["system"]["autorun_recognition"]:
            QTimer.singleShot(300, self.start)

    # --------------------------------------------------------------- служебное
    def _act(self, menu, text, fn):
        a = QAction(text, self)
        a.triggered.connect(fn)
        menu.addAction(a)
        return a

    def _open_path(self, p):
        p = str(p)
        if os.path.isdir(p) or os.path.isfile(p):
            if os.name == "nt":
                os.startfile(p)
            else:
                QMessageBox.information(self, "Путь", p)
        else:
            QMessageBox.information(self, "Нет файла", f"Пока не создано:\n{p}")

    def _apply_autostart(self):
        try:
            settings_mod.set_autostart(self.s["system"]["autostart"], sys.executable)
        except Exception as e:
            log(f"Автозапуск не настроен: {e}")

    # --------------------------------------------------------------- старт/стоп
    def start(self):
        if self.engine and self.engine.running:
            return
        if not self.s["camera"]["ip"] and not self.s["camera"]["rtsp_url"]:
            self.open_settings()
            return
        self.engine = Engine(self.s, on_pass=self.bridge.passed.emit,
                             on_status=self.bridge.status.emit)
        self.engine.start()
        self.start_btn.setText("■  Остановить")

    def stop(self):
        if self.engine:
            eng, self.engine = self.engine, None
            self.state_lbl.setText("Останавливаю…")
            QApplication.processEvents()
            eng.stop()
        self.start_btn.setText("▶  Начать распознавание")
        self._on_status("Остановлено", False)
        self.video.clear()
        self.video.setText("Нет изображения")

    def toggle(self):
        if self.engine and self.engine.running:
            self.stop()
        else:
            self.start()

    def restart(self):
        was = bool(self.engine and self.engine.running)
        self.stop()
        if was:
            self.start()

    # --------------------------------------------------------------- события
    def _tick(self):
        eng = self.engine
        if not eng or not self.isVisible() or self.isMinimized():
            return
        frame = eng.frame()
        if frame is not None:
            now = time.time()
            boxes = [b for b in eng.boxes if now - b[5] < 1.0]
            self.video.show_frame(frame, self.s["recognition"].get("roi"), boxes)
        self.statusBar().showMessage(
            f"Обработка: {eng.fps:.1f} кадр/с   •   Журнал: {self.s['output']['excel_file']}")

    def _watchdog(self):
        """Если распознавание зависло или камера давно молчит — перезапуск."""
        eng = self.engine
        if not eng or not eng.running:
            return
        now = time.time()
        reason = ""
        if eng.thread and not eng.thread.is_alive():
            reason = "поток распознавания остановился"
        elif eng.alive_ts and now - eng.alive_ts > 90:
            reason = f"распознавание не отвечает {now - eng.alive_ts:.0f} с"
        elif eng.cam and eng.cam.ts and now - eng.cam.ts > 180:
            reason = f"нет кадров с камеры {now - eng.cam.ts:.0f} с"
        if reason:
            log(f"Сторож: {reason} — перезапускаю распознавание")
            self.engine = None
            threading.Thread(target=eng.stop, daemon=True).start()
            self.start()

    def _on_status(self, text, ok):
        color = "#0a7f2e" if ok else "#b00020"
        if text in ("Остановлено",) or "Загрузка" in text:
            color = "#555"
        self.state_lbl.setText(f"<span style='color:{color}'>●</span> {text}")
        self.tray.setToolTip(f"PlateReader — {text}")

    def _on_log(self, line):
        self.log_view.appendPlainText(line)

    def _on_pass(self, d):
        self._add_row(d, top=True)
        self._update_count()
        if self.s["system"]["notify"]:
            self.tray.showMessage("Проезд", f"{d['plate']}  {d['time']}",
                                  QSystemTrayIcon.Information, 4000)

    def _add_row(self, d, top=False):
        row = 0 if top else self.table.rowCount()
        self.table.insertRow(row)
        conf = d.get("conf")
        try:
            conf_s = f"{float(conf):.0%}"
        except Exception:
            conf_s = str(conf or "")
        if conf in (None, ""):
            conf_s = ""
        vals = [d["date"], d["time"], d["plate"], conf_s, d.get("note", "") or ""]
        unrec = d["plate"] == "не распознан"
        for c, v in enumerate(vals):
            it = QTableWidgetItem(str(v))
            if c == 2:
                it.setFont(QFont("Arial", 11, QFont.Bold))
                it.setData(Qt.UserRole, d.get("snap", ""))
            if unrec:
                it.setForeground(QColor("#b00020"))
            if c == 4:
                it.setToolTip(str(v))
            self.table.setItem(row, c, it)
        if self.table.rowCount() > 500:
            self.table.removeRow(self.table.rowCount() - 1)
        if top:
            self.table.selectRow(0)

    def _load_history(self):
        from .engine import ExcelLog
        path = self.s["output"]["excel_file"]
        if not os.path.exists(path):
            return
        try:
            rows = ExcelLog(path).last_rows(300)
        except Exception:
            rows = []
        for d in reversed(rows):
            self._add_row(d)
        self._update_count()

    def _update_count(self):
        from datetime import datetime
        today = datetime.now().strftime("%d.%m.%Y")
        n = sum(1 for r in range(self.table.rowCount())
                if self.table.item(r, 0) and self.table.item(r, 0).text() == today)
        self.count_lbl.setText(f"Проездов сегодня: {n}")

    def _selected_snap(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return ""
        it = self.table.item(rows[0].row(), 2)
        return it.data(Qt.UserRole) if it else ""

    def _show_snap(self):
        p = self._selected_snap()
        img = load_jpg(p) if p and os.path.exists(p) else None
        if img is None:
            self.snap.setPixmap(QPixmap())
            self.snap.setText("Снимка нет")
            return
        self.snap.setPixmap(to_pixmap(img, self.snap.width(), self.snap.height()))

    def _open_snap(self, *_):
        p = self._selected_snap()
        if p and os.path.exists(p):
            self._open_path(p)

    # --------------------------------------------------------------- действия
    def open_excel(self):
        self._open_path(self.s["output"]["excel_file"])

    def open_snaps(self):
        self._open_path(self.s["output"]["snapshot_dir"])

    def open_log(self):
        self._open_path(paths.log_file())

    def about(self):
        QMessageBox.about(self, "О программе",
                          f"<b>PlateReader {__version__}</b><br>Распознавание автомобильных "
                          "номеров с IP-камеры и журнал проездов в Excel.<br><br>"
                          f"Настройки: {paths.settings_file()}")

    def open_settings(self):
        dlg = SettingsDialog(self.s, self)
        if dlg.exec() != QDialog.Accepted:
            return
        new = dlg.values()
        changed = any(new[k] != self.s[k] for k in ("camera", "recognition", "output"))
        self.s.clear()
        self.s.update(new)
        settings_mod.save(self.s)
        self._apply_autostart()
        if changed:
            if self.engine and self.engine.running:
                self.restart()
            else:
                self.start()

    def open_roi(self):
        frame = self.engine.frame() if self.engine else None
        if frame is None:
            self.statusBar().showMessage("Беру кадр с камеры…")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                frame = grab_frame(rtsp_url(self.s["camera"]))
            finally:
                QApplication.restoreOverrideCursor()
        if frame is None:
            QMessageBox.warning(self, "Камера", "Не удалось получить кадр с камеры. "
                                "Проверьте настройки камеры.")
            return
        dlg = RoiDialog(frame.copy(), self.s["recognition"].get("roi"), self)
        if dlg.exec() == QDialog.Accepted:
            self.s["recognition"]["roi"] = dlg.result_roi
            settings_mod.save(self.s)
            log(f"Область распознавания: {dlg.result_roi or 'весь кадр'}")
            self.restart()

    def _first_run(self):
        QMessageBox.information(
            self, "Добро пожаловать",
            "Укажите адрес камеры, логин и пароль. Затем выделите область, "
            "где проезжают номера машин.")
        self.open_settings()
        if self.engine and self.engine.running:
            QTimer.singleShot(2500, self.open_roi)

    # --------------------------------------------------------------- окно
    def show_normal(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if not self.quitting and self.s["system"]["tray_on_close"]:
            e.ignore()
            self.hide()
            if not getattr(self, "_tray_hint", False):
                self._tray_hint = True
                self.tray.showMessage("PlateReader работает",
                                      "Программа продолжает распознавать номера. "
                                      "Открыть — двойной щелчок по значку у часов.",
                                      QSystemTrayIcon.Information, 5000)
            return
        self.quitting = True
        self.stop()
        self.tray.hide()
        e.accept()
        QApplication.quit()

    def quit_app(self):
        if self.engine and self.engine.running:
            if QMessageBox.question(self, "Выход", "Остановить распознавание и выйти?") \
                    != QMessageBox.Yes:
                return
        self.quitting = True
        self.close()
