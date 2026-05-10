#!/usr/bin/env python3

import signal
import subprocess
import sys

import pulsectl

from PyQt6.QtCore import Qt, QEvent, QObject
from PyQt6.QtGui import QAction, QPainter, QColor, QPen, QCursor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QSlider, QSystemTrayIcon, QMenu,
)

LIGHT = {"bg": "#f5f5f5", "fg": "#111111", "border": "#cfcfcf", "accent": "#3b82f6", "mark": "#ef4444"}
DARK  = {"bg": "#232323", "fg": "#f5f5f5", "border": "#3a3a3a", "accent": "#60a5fa", "mark": "#ff7070"}


def is_dark(app):
    c = app.palette().window().color()
    return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) < 128


class VolumeSlider(QSlider):
    def __init__(self, theme):
        super().__init__(Qt.Orientation.Horizontal)
        self.theme = theme
        self.setRange(0, 150)

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        pen = QPen(QColor(self.theme["mark"]))
        pen.setWidth(2)
        p.setPen(pen)
        x = int((100 / 150) * self.width())
        p.drawLine(x, 4, x, self.height() - 4)
        p.end()


class VolumePopup(QWidget):
    def __init__(self):
        super().__init__()
        self.pulse = pulsectl.Pulse("tray-volume")
        self.sinks = []
        self.sources = []

        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setFixedWidth(320)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        def add_section(label):
            combo = QComboBox()
            slider = VolumeSlider(DARK)
            pct = QLabel("0%")
            pct.setFixedWidth(40)
            pct.setAlignment(Qt.AlignmentFlag.AlignRight)
            row = QHBoxLayout()
            row.addWidget(slider)
            row.addWidget(pct)
            root.addWidget(QLabel(label))
            root.addWidget(combo)
            root.addLayout(row)
            return combo, slider, pct

        self.out_combo, self.out_slider, self.out_pct = add_section("Output")
        self.in_combo,  self.in_slider,  self.in_pct  = add_section("Input")

        self.out_combo.currentIndexChanged.connect(self._change_sink)
        self.in_combo.currentIndexChanged.connect(self._change_source)
        self.out_slider.valueChanged.connect(self._change_sink_vol)
        self.in_slider.valueChanged.connect(self._change_source_vol)

        self.apply_theme(DARK)

    def apply_theme(self, t):
        self.theme = t
        self.out_slider.set_theme(t)
        self.in_slider.set_theme(t)
        self.setStyleSheet(f"""
            QWidget      {{ background:{t["bg"]}; color:{t["fg"]}; border:1px solid {t["border"]}; font-size:13px; }}
            QLabel        {{ border:none; }}
            QComboBox, QSlider {{ min-height:24px; border:none; }}
            QComboBox     {{ border:1px solid {t["border"]}; padding:4px 8px; border-radius:6px; background:{t["bg"]}; }}
            QComboBox::drop-down {{ border:none; }}
            QSlider::groove:horizontal {{ height:6px; background:{t["border"]}; border-radius:3px; }}
            QSlider::handle:horizontal {{ background:{t["accent"]}; width:16px; margin:-5px 0; border-radius:8px; }}
        """)

    def refresh(self):
        self.sinks = self.pulse.sink_list()
        self.sources = [s for s in self.pulse.source_list()
                        if not getattr(s, "monitor_of_sink", None) or s.monitor_of_sink == 4294967295]
        info = self.pulse.server_info()

        def reload_combo(combo, items, name_attr, default_name):
            combo.blockSignals(True)
            combo.clear()
            idx = 0
            for i, item in enumerate(items):
                combo.addItem(item.description)
                if getattr(item, name_attr) == default_name:
                    idx = i
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
            return idx

        si = reload_combo(self.out_combo, self.sinks,   "name", info.default_sink_name)
        so = reload_combo(self.in_combo,  self.sources, "name", info.default_source_name)

        def set_vol(slider, pct_label, items, idx):
            if not items:
                return
            slider.blockSignals(True)
            v = int(items[idx].volume.value_flat * 100)
            slider.setValue(v)
            pct_label.setText(f"{v}%")
            slider.blockSignals(False)

        set_vol(self.out_slider, self.out_pct, self.sinks,   si)
        set_vol(self.in_slider,  self.in_pct,  self.sources, so)

    def focusOutEvent(self, event):
        self.hide()
        super().focusOutEvent(event)

    def _change_sink(self, idx):
        if self.sinks and idx >= 0:
            try: self.pulse.default_set(self.sinks[idx])
            except Exception as e: print(e)

    def _change_source(self, idx):
        if self.sources and idx >= 0:
            try: self.pulse.default_set(self.sources[idx])
            except Exception as e: print(e)

    def _change_sink_vol(self, value):
        self.out_pct.setText(f"{value}%")
        idx = self.out_combo.currentIndex()
        if self.sinks and idx >= 0:
            try:
                self.pulse.volume_set_all_chans(self.sinks[idx], value / 100)
                subprocess.Popen(
                    ["canberra-gtk-play", "-i", "audio-volume-change", "-d", "volume-applet"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception as e: print(e)

    def _change_source_vol(self, value):
        self.in_pct.setText(f"{value}%")
        idx = self.in_combo.currentIndex()
        if self.sources and idx >= 0:
            try: self.pulse.volume_set_all_chans(self.sources[idx], value / 100)
            except Exception as e: print(e)


class TrayApp(QObject):
    def __init__(self):
        super().__init__()
        self.app = QApplication(sys.argv)
        QApplication.setQuitOnLastWindowClosed(False)

        self.popup = VolumePopup()
        self.tray  = QSystemTrayIcon()
        self.tray.setToolTip("Volume")
        self.tray.setVisible(True)

        self._update_theme()
        self.app.installEventFilter(self)
        self.tray.activated.connect(self._on_activated)

        menu = QMenu()
        a_refresh = QAction("Refresh")
        a_refresh.triggered.connect(self.popup.refresh)
        a_quit = QAction("Quit")
        a_quit.triggered.connect(self._quit)
        menu.addAction(a_refresh)
        menu.addSeparator()
        menu.addAction(a_quit)
        self.tray.setContextMenu(menu)

    def _make_icon(self, dark):
        px = QPixmap(64, 64)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        pen = QPen(QColor("#111111") if dark else QColor("#ffffff"))
        pen.setWidth(5)
        p.setPen(pen)
        p.drawLine(16, 24, 28, 24)
        p.drawLine(16, 40, 28, 40)
        p.drawLine(16, 24, 16, 40)
        p.drawLine(28, 24, 40, 16)
        p.drawLine(28, 40, 40, 48)
        p.drawLine(40, 16, 40, 48)
        p.drawArc(36, 18, 18, 28, -40 * 16, 80 * 16)
        p.drawArc(42, 12, 18, 40, -40 * 16, 80 * 16)
        p.end()
        return QIcon(px)

    def _update_theme(self):
        dark = is_dark(self.app)
        self.popup.apply_theme(DARK if dark else LIGHT)
        self.tray.setIcon(self._make_icon(dark))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_theme()
        return False

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.popup.isVisible():
                self.popup.hide()
            else:
                self._show_popup()

    def _show_popup(self):
        self.popup.refresh()
        self.popup.adjustSize()

        geo = self.tray.geometry()
        if geo.isNull():
            pos = QCursor.pos()
            x = pos.x() - self.popup.width() // 2
            y = pos.y() - self.popup.height() - 12
        else:
            x = geo.center().x() - self.popup.width() // 2
            y = geo.top() - self.popup.height() - 8

        self.popup.move(x, y)
        self.popup.show()
        self.popup.raise_()
        self.popup.activateWindow()

    def _quit(self):
        self.tray.hide()
        self.app.quit()

    def run(self):
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        sys.exit(self.app.exec())


if __name__ == "__main__":
    TrayApp().run()