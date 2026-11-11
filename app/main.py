"""Точка входа PlateReader."""

import os
import sys
import traceback


def selftest():
    """Проверка сборки: модели загружаются и кадр обрабатывается."""
    import numpy as np
    from app.engine import Recognizer, fix_ru, vote
    for q in ("accurate", "fast"):
        r = Recognizer(q)
        r.read(np.zeros((1080, 1920, 3), np.uint8))
    assert fix_ru("A123BC77")[1] == "А123ВС77"
    assert vote([{"lat": "A123BC77", "conf": .9, "chars": [.9] * 8, "raw": ""}])[0] == "А123ВС77"
    print("SELFTEST OK")
    return 0


def uitest():
    """Проверка окон без экрана (для сборки)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    from PySide6.QtWidgets import QApplication
    from app import settings as st
    from app.ui import MainWindow, RoiDialog, SettingsDialog
    app = QApplication(sys.argv)
    s, _ = st.load()
    s["system"]["autorun_recognition"] = False
    s["system"]["autostart"] = False
    w = MainWindow(s)
    w.show()
    d = SettingsDialog(s, w)
    d.values()
    frame = np.zeros((1080, 1920, 3), np.uint8)
    r = RoiDialog(frame, [100, 100, 900, 600], w)
    assert r.canvas.roi() is not None
    w._on_pass({"date": "01.01.2026", "time": "12:00:00", "plate": "А123ВС77",
                "conf": 0.93, "agree": "2/2", "snap": ""})
    w.video.show_frame(frame, [100, 100, 900, 600], [(10, 10, 100, 40, "А123ВС77", 0)])
    app.processEvents()
    print("UITEST OK")
    return 0


def main():
    # в оконной сборке нет консоли — некоторым библиотекам нужен stdout
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if "--selftest" in sys.argv:
        return selftest()
    if "--uitest" in sys.argv:
        return uitest()

    from PySide6.QtCore import Qt
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app import settings as st
    from app.log import log
    from app.ui import MainWindow, app_icon

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("PlateReader")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")

    # одна копия программы: вторая просто показывает окно первой
    name = "PlateReader-single-instance"
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(500):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(500)
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)

    s, first = st.load()
    log("Программа запущена")
    win = MainWindow(s, first_run=first)

    def on_conn():
        c = server.nextPendingConnection()
        if c:
            c.waitForReadyRead(300)
            win.show_normal()
    server.newConnection.connect(on_conn)

    if "--minimized" in sys.argv or (s["system"]["start_minimized"] and not first):
        win.hide()
    else:
        win.show()

    def excepthook(t, v, tb):
        log("ОШИБКА:\n" + "".join(traceback.format_exception(t, v, tb)))
        try:
            QMessageBox.critical(win, "Ошибка", f"{v}\n\nПодробности — Справка → Журнал работы.")
        except Exception:
            pass
    sys.excepthook = excepthook
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
