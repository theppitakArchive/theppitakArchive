#!/usr/bin/env python3
"""YouTube Video Editor GUI - วาง URL → เล่นในโปรแกรม → ตัด → Export MP4"""

import os
import sys
import threading
import subprocess
import tempfile
from pathlib import Path

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QLineEdit, QLabel, QSlider, QComboBox, QFrame,
        QFileDialog, QProgressBar, QSplitter, QMessageBox, QSpinBox,
        QGroupBox, QSizePolicy
    )
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
    from PyQt5.QtGui import QFont, QPalette, QColor, QIcon
except ImportError:
    print("กรุณาติดตั้ง PyQt5: pip install PyQt5")
    sys.exit(1)

try:
    import vlc
    VLC_AVAILABLE = True
except ImportError:
    VLC_AVAILABLE = False

try:
    import yt_dlp
except ImportError:
    print("กรุณาติดตั้ง yt-dlp: pip install yt-dlp")
    sys.exit(1)


# ──────────────────────────────────────────────
# Worker threads
# ──────────────────────────────────────────────

class DownloadWorker(QObject):
    finished = pyqtSignal(str)   # path
    error    = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, url: str, quality: str, out_dir: str):
        super().__init__()
        self.url = url
        self.quality = quality
        self.out_dir = out_dir

    def run(self):
        quality_map = {
            "360p":  "bestvideo[height<=360]+bestaudio/best[height<=360]",
            "480p":  "bestvideo[height<=480]+bestaudio/best[height<=480]",
            "720p":  "bestvideo[height<=720]+bestaudio/best[height<=720]",
            "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "Best":  "bestvideo+bestaudio/best",
        }
        fmt = quality_map.get(self.quality, quality_map["Best"])
        os.makedirs(self.out_dir, exist_ok=True)

        def hook(d):
            if d["status"] == "downloading":
                pct = d.get("_percent_str", "").strip()
                spd = d.get("_speed_str", "").strip()
                self.progress.emit(f"กำลังดาวน์โหลด {pct}  {spd}")
            elif d["status"] == "finished":
                self.progress.emit("กำลังรวมไฟล์...")

        opts = {
            "format": fmt,
            "outtmpl": os.path.join(self.out_dir, "%(title)s.%(ext)s"),
            "merge_output_format": "mp4",
            "quiet": True,
            "noplaylist": True,
            "progress_hooks": [hook],
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=True)
                path = ydl.prepare_filename(info)
                if not Path(path).exists():
                    path = str(Path(path).with_suffix(".mp4"))
            self.finished.emit(path)
        except Exception as e:
            self.error.emit(str(e))


class ExportWorker(QObject):
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, src: str, dst: str, start: float, end: float, crf: int, res: str):
        super().__init__()
        self.src   = src
        self.dst   = dst
        self.start = start
        self.end   = end
        self.crf   = crf
        self.res   = res

    def run(self):
        try:
            cmd = ["ffmpeg", "-y",
                   "-ss", str(self.start),
                   "-to", str(self.end),
                   "-i", self.src]

            vf = []
            if self.res and self.res != "Original":
                w, h = self.res.split("x")
                vf.append(f"scale={w}:{h}")
            if vf:
                cmd += ["-vf", ",".join(vf)]

            cmd += ["-c:v", "libx264", "-crf", str(self.crf),
                    "-preset", "slow", "-c:a", "aac", "-b:a", "192k", self.dst]

            self.progress.emit("กำลัง Export MP4...")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                self.error.emit(result.stderr[-500:])
            else:
                self.finished.emit(self.dst)
        except Exception as e:
            self.error.emit(str(e))


# ──────────────────────────────────────────────
# Video Player Widget (VLC)
# ──────────────────────────────────────────────

class VideoWidget(QWidget):
    duration_ready = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(360)
        self.setStyleSheet("background: #0a0a0a;")
        self._instance  = None
        self._player    = None
        self._media     = None
        self._duration  = 0.0
        self._timer     = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(200)
        self._duration_emitted = False

        if VLC_AVAILABLE:
            self._instance = vlc.Instance("--no-xlib")
            self._player   = self._instance.media_player_new()
            if sys.platform == "win32":
                self._player.set_hwnd(int(self.winId()))
            elif sys.platform == "darwin":
                self._player.set_nsobject(int(self.winId()))
            else:
                self._player.set_xwindow(self.winId())

        # placeholder label
        self._label = QLabel("วางลิงก์ YouTube แล้วกด โหลดวิดีโอ", self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color: #555; font-size: 16px;")
        self._label.setGeometry(0, 0, self.width(), self.height())

    def resizeEvent(self, e):
        self._label.setGeometry(0, 0, self.width(), self.height())

    def load(self, path: str):
        if not self._player:
            return
        self._duration_emitted = False
        self._label.hide()
        self._media = self._instance.media_new(path)
        self._player.set_media(self._media)
        self._player.play()

    def play_pause(self):
        if self._player:
            self._player.pause()

    def stop(self):
        if self._player:
            self._player.stop()

    def seek(self, seconds: float):
        if self._player and self._duration > 0:
            self._player.set_time(int(seconds * 1000))

    def get_position(self) -> float:
        if self._player:
            t = self._player.get_time()
            return t / 1000.0 if t >= 0 else 0.0
        return 0.0

    def get_duration(self) -> float:
        return self._duration

    def set_volume(self, v: int):
        if self._player:
            self._player.audio_set_volume(v)

    def _poll(self):
        if not self._player:
            return
        dur = self._player.get_length()
        if dur > 0:
            self._duration = dur / 1000.0
            if not self._duration_emitted:
                self._duration_emitted = True
                self.duration_ready.emit(self._duration)


# ──────────────────────────────────────────────
# Main Window
# ──────────────────────────────────────────────

DARK = """
QMainWindow, QWidget {
    background: #1a1a2e;
    color: #e0e0e0;
    font-family: 'Segoe UI', sans-serif;
}
QGroupBox {
    border: 1px solid #2d2d4e;
    border-radius: 8px;
    margin-top: 8px;
    padding: 8px;
    font-weight: bold;
    color: #9d8fff;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; }
QPushButton {
    background: #2d2d4e;
    color: #e0e0e0;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
}
QPushButton:hover  { background: #3d3d6e; }
QPushButton:pressed { background: #5a4fff; }
QPushButton#primary {
    background: #5a4fff;
    font-weight: bold;
    font-size: 14px;
    padding: 10px 24px;
}
QPushButton#primary:hover  { background: #7a6fff; }
QPushButton#danger { background: #8b2020; }
QPushButton#danger:hover   { background: #b02828; }
QPushButton#success { background: #1a5c2a; }
QPushButton#success:hover  { background: #226b34; }
QLineEdit, QComboBox, QSpinBox {
    background: #0d0d1a;
    border: 1px solid #3d3d6e;
    border-radius: 6px;
    padding: 6px 10px;
    color: #e0e0e0;
    font-size: 13px;
}
QLineEdit:focus { border-color: #5a4fff; }
QSlider::groove:horizontal {
    height: 4px;
    background: #2d2d4e;
    border-radius: 2px;
}
QSlider::sub-page:horizontal { background: #5a4fff; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #fff;
    border: 2px solid #5a4fff;
    width: 14px; height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QProgressBar {
    background: #0d0d1a;
    border: 1px solid #3d3d6e;
    border-radius: 4px;
    height: 8px;
    text-align: center;
}
QProgressBar::chunk { background: #5a4fff; border-radius: 4px; }
QLabel#status { color: #9d8fff; font-size: 12px; }
QLabel#time   { color: #aaa;    font-family: monospace; }
"""


def fmt_time(secs: float) -> str:
    secs = max(0, int(secs))
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YouTube Video Editor")
        self.resize(1100, 760)
        self.setStyleSheet(DARK)

        self._video_path = ""
        self._duration   = 0.0
        self._seeking    = False
        self._download_thread = None
        self._export_thread   = None
        self._download_dir    = str(Path.home() / "Downloads" / "YT_Editor")

        self._build_ui()

        # position update timer
        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._update_position)
        self._pos_timer.start(300)

    # ── UI ──────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # ── URL bar ──
        url_box = QGroupBox("YouTube URL")
        url_lay = QHBoxLayout(url_box)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("วาง URL YouTube ที่นี่...")
        self.url_input.returnPressed.connect(self._on_download)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["1080p", "720p", "480p", "360p", "Best"])
        self.quality_combo.setFixedWidth(100)
        self.dl_btn = QPushButton("⬇  โหลดวิดีโอ")
        self.dl_btn.setObjectName("primary")
        self.dl_btn.setFixedWidth(160)
        self.dl_btn.clicked.connect(self._on_download)
        self.open_btn = QPushButton("📂 เปิดไฟล์")
        self.open_btn.clicked.connect(self._open_local_file)
        url_lay.addWidget(self.url_input)
        url_lay.addWidget(self.quality_combo)
        url_lay.addWidget(self.dl_btn)
        url_lay.addWidget(self.open_btn)
        root.addWidget(url_box)

        # ── splitter: player | controls ──
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, stretch=1)

        # left: video + transport
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)

        self.video = VideoWidget()
        self.video.duration_ready.connect(self._on_duration_ready)
        left_lay.addWidget(self.video, stretch=1)

        # seek bar
        self.seek_bar = QSlider(Qt.Horizontal)
        self.seek_bar.setRange(0, 1000)
        self.seek_bar.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek_bar.sliderReleased.connect(self._on_seek)
        left_lay.addWidget(self.seek_bar)

        # time labels + transport buttons
        transport = QHBoxLayout()
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("time")
        transport.addWidget(self.time_label)
        transport.addStretch()
        self.play_btn = QPushButton("▶  Play / Pause")
        self.play_btn.clicked.connect(self.video.play_pause)
        transport.addWidget(self.play_btn)

        vol_lbl = QLabel("🔊")
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(80)
        self.vol_slider.setFixedWidth(90)
        self.vol_slider.valueChanged.connect(self.video.set_volume)
        transport.addWidget(vol_lbl)
        transport.addWidget(self.vol_slider)
        left_lay.addLayout(transport)

        splitter.addWidget(left)

        # right: edit panel
        right = QWidget()
        right.setFixedWidth(290)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(4, 0, 0, 0)
        right_lay.setSpacing(10)

        # ── Trim ──
        trim_box = QGroupBox("✂  ตัดต่อ (Trim)")
        trim_lay = QVBoxLayout(trim_box)

        row_in = QHBoxLayout()
        self.start_lbl = QLabel("เริ่ม:")
        self.start_time = QLabel("00:00")
        self.start_time.setObjectName("time")
        self.set_start_btn = QPushButton("📍 Set")
        self.set_start_btn.clicked.connect(self._set_start)
        row_in.addWidget(self.start_lbl)
        row_in.addWidget(self.start_time)
        row_in.addStretch()
        row_in.addWidget(self.set_start_btn)
        trim_lay.addLayout(row_in)

        # start slider
        self.start_slider = QSlider(Qt.Horizontal)
        self.start_slider.setRange(0, 1000)
        self.start_slider.valueChanged.connect(self._start_slider_moved)
        trim_lay.addWidget(self.start_slider)

        row_out = QHBoxLayout()
        self.end_lbl = QLabel("สิ้นสุด:")
        self.end_time = QLabel("00:00")
        self.end_time.setObjectName("time")
        self.set_end_btn = QPushButton("📍 Set")
        self.set_end_btn.clicked.connect(self._set_end)
        row_out.addWidget(self.end_lbl)
        row_out.addWidget(self.end_time)
        row_out.addStretch()
        row_out.addWidget(self.set_end_btn)
        trim_lay.addLayout(row_out)

        # end slider
        self.end_slider = QSlider(Qt.Horizontal)
        self.end_slider.setRange(0, 1000)
        self.end_slider.setValue(1000)
        self.end_slider.valueChanged.connect(self._end_slider_moved)
        trim_lay.addWidget(self.end_slider)

        # preview trim btn
        self.preview_btn = QPushButton("▶  Preview ช่วงที่ตัด")
        self.preview_btn.clicked.connect(self._preview_trim)
        trim_lay.addWidget(self.preview_btn)

        right_lay.addWidget(trim_box)

        # ── Quality ──
        q_box = QGroupBox("⚙  คุณภาพ Export")
        q_lay = QVBoxLayout(q_box)

        q_lay.addWidget(QLabel("ความละเอียด:"))
        self.res_combo = QComboBox()
        self.res_combo.addItems(["Original", "1920x1080", "1280x720", "854x480", "640x360"])
        q_lay.addWidget(self.res_combo)

        crf_row = QHBoxLayout()
        crf_row.addWidget(QLabel("CRF (คุณภาพ):"))
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(18)
        self.crf_spin.setToolTip("0 = ดีที่สุด / ใหญ่ที่สุด   51 = แย่ที่สุด")
        crf_row.addWidget(self.crf_spin)
        q_lay.addLayout(crf_row)

        # CRF description
        self.crf_desc = QLabel("คุณภาพสูงมาก (แนะนำ: 18-23)")
        self.crf_desc.setStyleSheet("color: #9d8fff; font-size: 11px;")
        q_lay.addWidget(self.crf_desc)
        self.crf_spin.valueChanged.connect(self._update_crf_desc)

        right_lay.addWidget(q_box)

        # ── Export ──
        exp_box = QGroupBox("💾  Export")
        exp_lay = QVBoxLayout(exp_box)
        self.export_btn = QPushButton("🎬  Export MP4")
        self.export_btn.setObjectName("success")
        self.export_btn.setMinimumHeight(44)
        self.export_btn.clicked.connect(self._on_export)
        exp_lay.addWidget(self.export_btn)
        right_lay.addWidget(exp_box)

        right_lay.addStretch()

        # status
        self.status_lbl = QLabel("พร้อมใช้งาน")
        self.status_lbl.setObjectName("status")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        right_lay.addWidget(self.status_lbl)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)   # indeterminate
        self.progress_bar.setVisible(False)
        right_lay.addWidget(self.progress_bar)

        splitter.addWidget(right)
        splitter.setSizes([780, 290])

    # ── slots ────────────────────────────────

    def _on_download(self):
        url = self.url_input.text().strip()
        if not url:
            return
        self._set_status("กำลังเริ่มดาวน์โหลด...", busy=True)
        self.dl_btn.setEnabled(False)

        worker = DownloadWorker(url, self.quality_combo.currentText(), self._download_dir)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(lambda msg: self._set_status(msg))
        worker.finished.connect(self._on_downloaded)
        worker.error.connect(self._on_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._download_thread = thread
        thread.start()

    def _on_downloaded(self, path: str):
        self.dl_btn.setEnabled(True)
        self._set_status(f"ดาวน์โหลดสำเร็จ: {Path(path).name}", busy=False)
        self._load_video(path)

    def _open_local_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "เลือกไฟล์วิดีโอ", str(Path.home()),
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm)"
        )
        if path:
            self._load_video(path)

    def _load_video(self, path: str):
        self._video_path = path
        self.setWindowTitle(f"YouTube Video Editor — {Path(path).name}")
        if VLC_AVAILABLE:
            self.video.load(path)
        else:
            self._set_status(f"โหลดแล้ว: {Path(path).name}  (ติดตั้ง python-vlc เพื่อเล่นในโปรแกรม)")
            # fallback: try ffprobe for duration
            self._probe_duration(path)

    def _probe_duration(self, path: str):
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "default=nw=1:nk=1", path],
                capture_output=True, text=True
            )
            dur = float(r.stdout.strip())
            self._on_duration_ready(dur)
        except Exception:
            pass

    def _on_duration_ready(self, dur: float):
        self._duration = dur
        self.end_slider.setValue(1000)
        self.end_time.setText(fmt_time(dur))
        self.start_time.setText("00:00")
        self._set_status(f"วิดีโอพร้อม  ความยาว: {fmt_time(dur)}")

    def _update_position(self):
        if not VLC_AVAILABLE or self._seeking:
            return
        pos = self.video.get_position()
        dur = self.video.get_duration() or self._duration
        if dur > 0:
            self.seek_bar.blockSignals(True)
            self.seek_bar.setValue(int(pos / dur * 1000))
            self.seek_bar.blockSignals(False)
        self.time_label.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")

    def _on_seek(self):
        self._seeking = False
        if self._duration > 0:
            target = self.seek_bar.value() / 1000.0 * self._duration
            self.video.seek(target)

    def _set_start(self):
        pos = self.video.get_position() if VLC_AVAILABLE else 0
        self.start_time.setText(fmt_time(pos))
        if self._duration > 0:
            self.start_slider.blockSignals(True)
            self.start_slider.setValue(int(pos / self._duration * 1000))
            self.start_slider.blockSignals(False)

    def _set_end(self):
        pos = self.video.get_position() if VLC_AVAILABLE else self._duration
        self.end_time.setText(fmt_time(pos))
        if self._duration > 0:
            self.end_slider.blockSignals(True)
            self.end_slider.setValue(int(pos / self._duration * 1000))
            self.end_slider.blockSignals(False)

    def _start_slider_moved(self, v: int):
        if self._duration > 0:
            t = v / 1000.0 * self._duration
            self.start_time.setText(fmt_time(t))

    def _end_slider_moved(self, v: int):
        if self._duration > 0:
            t = v / 1000.0 * self._duration
            self.end_time.setText(fmt_time(t))

    def _preview_trim(self):
        if not VLC_AVAILABLE or not self._video_path:
            return
        start = self.start_slider.value() / 1000.0 * self._duration
        self.video.seek(start)
        self.video.play_pause()

    def _update_crf_desc(self, v: int):
        if v <= 15:
            desc = "คุณภาพสูงมาก (ไฟล์ใหญ่)"
        elif v <= 23:
            desc = "คุณภาพสูง (แนะนำ: 18-23)"
        elif v <= 28:
            desc = "คุณภาพปานกลาง"
        else:
            desc = "คุณภาพต่ำ (ไฟล์เล็ก)"
        self.crf_desc.setText(desc)

    def _on_export(self):
        if not self._video_path:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาโหลดวิดีโอก่อน")
            return

        start = self.start_slider.value() / 1000.0 * self._duration
        end   = self.end_slider.value()   / 1000.0 * self._duration
        if end <= start:
            QMessageBox.warning(self, "แจ้งเตือน", "เวลาสิ้นสุดต้องมากกว่าเวลาเริ่มต้น")
            return

        default_name = Path(self._video_path).stem + "_edit.mp4"
        out_path, _ = QFileDialog.getSaveFileName(
            self, "บันทึก MP4", str(Path(self._video_path).parent / default_name),
            "MP4 (*.mp4)"
        )
        if not out_path:
            return

        res = self.res_combo.currentText()
        if res == "Original":
            res = ""
        crf = self.crf_spin.value()

        self.export_btn.setEnabled(False)
        self._set_status("กำลัง Export...", busy=True)

        worker = ExportWorker(self._video_path, out_path, start, end, crf, res)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(lambda msg: self._set_status(msg))
        worker.finished.connect(self._on_export_done)
        worker.error.connect(self._on_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._export_thread = thread
        thread.start()

    def _on_export_done(self, path: str):
        self.export_btn.setEnabled(True)
        self._set_status(f"Export สำเร็จ: {Path(path).name}", busy=False)
        QMessageBox.information(self, "สำเร็จ", f"บันทึกไฟล์เสร็จแล้ว:\n{path}")

    def _on_error(self, msg: str):
        self.dl_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self._set_status(f"Error: {msg[:80]}", busy=False)
        QMessageBox.critical(self, "เกิดข้อผิดพลาด", msg)

    def _set_status(self, msg: str, busy: bool | None = None):
        self.status_lbl.setText(msg)
        if busy is True:
            self.progress_bar.setVisible(True)
        elif busy is False:
            self.progress_bar.setVisible(False)


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("YouTube Video Editor")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
