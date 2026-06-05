#!/usr/bin/env python3
"""Video Upscaler & Editor - เปิดไฟล์วิดีโอ → ตัด → เพิ่มความละเอียด/เฟรมเรท → Export MP4"""

import os
import sys
import subprocess
from pathlib import Path

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QLineEdit, QLabel, QSlider, QComboBox, QFileDialog,
        QProgressBar, QSplitter, QMessageBox, QSpinBox, QGroupBox, QCheckBox
    )
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
except ImportError:
    print("กรุณาติดตั้ง PyQt5: pip install PyQt5")
    sys.exit(1)

try:
    import vlc
    VLC_AVAILABLE = True
except Exception:
    VLC_AVAILABLE = False


# ──────────────────────────────────────────────
# Export worker (ffmpeg)
# ──────────────────────────────────────────────

class ExportWorker(QObject):
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, src, dst, start, end, crf, res, fps, interp, sharpen, preset):
        super().__init__()
        self.src     = src
        self.dst     = dst
        self.start   = start
        self.end     = end
        self.crf     = crf
        self.res     = res
        self.fps     = fps
        self.interp  = interp
        self.sharpen = sharpen
        self.preset  = preset

    def run(self):
        try:
            cmd = ["ffmpeg", "-y",
                   "-ss", str(self.start),
                   "-to", str(self.end),
                   "-i", self.src]

            vf = []
            # Upscale resolution (lanczos = ดีที่สุดสำหรับ upscale)
            if self.res and self.res != "Original":
                w, h = self.res.split("x")
                vf.append(f"scale={w}:{h}:flags=lanczos")

            # Sharpening filter
            if self.sharpen > 0:
                amount = self.sharpen / 10.0   # 0.1 - 1.5
                vf.append(f"unsharp=5:5:{amount}:5:5:0.0")

            # Frame rate boost
            if self.fps and self.fps != "Original":
                target = int(self.fps)
                if self.interp:
                    # motion interpolation (ช้าแต่นุ่มมาก)
                    vf.append(f"minterpolate=fps={target}:mi_mode=mci:mc_mode=aobmc:vsbmf=1")
                else:
                    vf.append(f"fps={target}")

            if vf:
                cmd += ["-vf", ",".join(vf)]

            cmd += ["-c:v", "libx264",
                    "-crf", str(self.crf),
                    "-preset", self.preset,
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    self.dst]

            self.progress.emit("กำลัง Export... (อาจใช้เวลานานถ้าเปิด Motion Interpolation)")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                self.error.emit(result.stderr[-800:])
            else:
                self.finished.emit(self.dst)
        except Exception as e:
            self.error.emit(str(e))


# ──────────────────────────────────────────────
# VLC Video Widget
# ──────────────────────────────────────────────

class VideoWidget(QWidget):
    duration_ready = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(360)
        self.setStyleSheet("background: #0a0a0a;")
        self._instance = self._player = self._media = None
        self._duration = 0.0
        self._dur_emitted = False

        if VLC_AVAILABLE:
            vlc_args = [
                "--no-xlib", "--quiet",
                "--avcodec-hw=none",      # ปิด hardware decode
                "--no-video-title-show",
            ]
            if sys.platform == "win32":
                vlc_args += ["--vout=direct3d9"]   # ใช้ D3D9 แทน D3D11
            self._instance = vlc.Instance(*vlc_args)
            self._player   = self._instance.media_player_new()
            if sys.platform == "win32":
                self._player.set_hwnd(int(self.winId()))
            elif sys.platform == "darwin":
                self._player.set_nsobject(int(self.winId()))
            else:
                self._player.set_xwindow(self.winId())

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color: #555; font-size: 16px;")
        if VLC_AVAILABLE:
            self._label.setText("กด '📂 เปิดไฟล์วิดีโอ' เพื่อเริ่ม")
        else:
            self._label.setText("ไม่พบ VLC — ติดตั้งจาก videolan.org\n(โปรแกรมยังตัด/Export ได้ปกติ)")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(200)

    def resizeEvent(self, e):
        self._label.setGeometry(0, 0, self.width(), self.height())

    def load(self, path):
        if not self._player: return
        self._dur_emitted = False
        self._label.hide()
        self._media = self._instance.media_new(path)
        self._player.set_media(self._media)
        self._player.play()

    def play_pause(self):
        if self._player: self._player.pause()

    def seek(self, secs):
        if self._player and self._duration > 0:
            self._player.set_time(int(secs * 1000))

    def get_position(self):
        if self._player:
            t = self._player.get_time()
            return t / 1000.0 if t >= 0 else 0.0
        return 0.0

    def get_duration(self):
        return self._duration

    def set_volume(self, v):
        if self._player: self._player.audio_set_volume(v)

    def _poll(self):
        if not self._player: return
        dur = self._player.get_length()
        if dur > 0:
            self._duration = dur / 1000.0
            if not self._dur_emitted:
                self._dur_emitted = True
                self.duration_ready.emit(self._duration)


# ──────────────────────────────────────────────
# Stylesheet
# ──────────────────────────────────────────────

DARK = """
QMainWindow, QWidget { background: #1a1a2e; color: #e0e0e0; font-family: 'Segoe UI'; }
QGroupBox {
    border: 1px solid #2d2d4e; border-radius: 8px;
    margin-top: 10px; padding: 10px; font-weight: bold; color: #9d8fff;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton {
    background: #2d2d4e; color: #e0e0e0; border: none;
    border-radius: 6px; padding: 8px 16px; font-size: 13px;
}
QPushButton:hover { background: #3d3d6e; }
QPushButton#primary { background: #5a4fff; font-weight: bold; padding: 10px 20px; }
QPushButton#primary:hover { background: #7a6fff; }
QPushButton#success { background: #1a7a3a; font-weight: bold; padding: 12px; font-size: 14px; }
QPushButton#success:hover { background: #229547; }
QLineEdit, QComboBox, QSpinBox {
    background: #0d0d1a; border: 1px solid #3d3d6e; border-radius: 6px;
    padding: 6px 10px; color: #e0e0e0; font-size: 13px;
}
QComboBox::drop-down { border: none; }
QSlider::groove:horizontal { height: 4px; background: #2d2d4e; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #5a4fff; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #fff; border: 2px solid #5a4fff;
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}
QProgressBar {
    background: #0d0d1a; border: 1px solid #3d3d6e;
    border-radius: 4px; height: 10px; text-align: center;
}
QProgressBar::chunk { background: #5a4fff; border-radius: 4px; }
QLabel#status { color: #9d8fff; font-size: 12px; }
QLabel#time { color: #aaa; font-family: 'Consolas', monospace; }
QLabel#info { color: #888; font-size: 11px; }
QCheckBox { color: #ccc; }
"""


def fmt_time(s):
    s = max(0, int(s))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


# ──────────────────────────────────────────────
# Main Window
# ──────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Upscaler & Editor")
        self.resize(1180, 780)
        self.setStyleSheet(DARK)

        self._video_path = ""
        self._duration = 0.0
        self._seeking = False
        self._src_info = ""
        self._build_ui()

        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._update_position)
        self._pos_timer.start(300)

    def _build_ui(self):
        cw = QWidget(); self.setCentralWidget(cw)
        root = QVBoxLayout(cw); root.setContentsMargins(12,12,12,12); root.setSpacing(8)

        # ── Top: file open ──
        top = QGroupBox("ไฟล์วิดีโอ")
        top_lay = QHBoxLayout(top)
        self.open_btn = QPushButton("📂 เปิดไฟล์วิดีโอ")
        self.open_btn.setObjectName("primary")
        self.open_btn.clicked.connect(self._open_file)
        self.file_lbl = QLabel("ยังไม่ได้เลือกไฟล์")
        self.file_lbl.setObjectName("info")
        top_lay.addWidget(self.open_btn)
        top_lay.addWidget(self.file_lbl, stretch=1)
        root.addWidget(top)

        # ── Splitter ──
        sp = QSplitter(Qt.Horizontal)
        root.addWidget(sp, stretch=1)

        # Left side
        left = QWidget(); ll = QVBoxLayout(left)
        ll.setContentsMargins(0,0,0,0); ll.setSpacing(6)

        self.video = VideoWidget()
        self.video.duration_ready.connect(self._on_duration)
        ll.addWidget(self.video, stretch=1)

        self.seek_bar = QSlider(Qt.Horizontal)
        self.seek_bar.setRange(0, 1000)
        self.seek_bar.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek_bar.sliderReleased.connect(self._on_seek)
        ll.addWidget(self.seek_bar)

        ctrl = QHBoxLayout()
        self.time_lbl = QLabel("00:00 / 00:00"); self.time_lbl.setObjectName("time")
        self.play_btn = QPushButton("▶  Play / Pause")
        self.play_btn.clicked.connect(self.video.play_pause)
        vol = QSlider(Qt.Horizontal); vol.setRange(0,100); vol.setValue(80); vol.setFixedWidth(100)
        vol.valueChanged.connect(self.video.set_volume)
        ctrl.addWidget(self.time_lbl); ctrl.addStretch()
        ctrl.addWidget(self.play_btn); ctrl.addWidget(QLabel("🔊")); ctrl.addWidget(vol)
        ll.addLayout(ctrl)
        sp.addWidget(left)

        # Right side: controls
        right = QWidget(); right.setFixedWidth(320)
        rl = QVBoxLayout(right); rl.setSpacing(10); rl.setContentsMargins(6,0,0,0)

        # Trim
        trim = QGroupBox("✂  ตัดต่อ")
        tl = QVBoxLayout(trim)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("เริ่ม:")); self.start_t = QLabel("00:00"); self.start_t.setObjectName("time")
        r1.addWidget(self.start_t); r1.addStretch()
        sb = QPushButton("📍 Set"); sb.clicked.connect(self._set_start); r1.addWidget(sb)
        tl.addLayout(r1)
        self.start_sl = QSlider(Qt.Horizontal); self.start_sl.setRange(0,1000)
        self.start_sl.valueChanged.connect(self._start_moved); tl.addWidget(self.start_sl)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("สิ้นสุด:")); self.end_t = QLabel("00:00"); self.end_t.setObjectName("time")
        r2.addWidget(self.end_t); r2.addStretch()
        eb = QPushButton("📍 Set"); eb.clicked.connect(self._set_end); r2.addWidget(eb)
        tl.addLayout(r2)
        self.end_sl = QSlider(Qt.Horizontal); self.end_sl.setRange(0,1000); self.end_sl.setValue(1000)
        self.end_sl.valueChanged.connect(self._end_moved); tl.addWidget(self.end_sl)

        pv = QPushButton("▶  Preview ช่วงที่ตัด"); pv.clicked.connect(self._preview)
        tl.addWidget(pv)
        rl.addWidget(trim)

        # Upscale: Resolution & FPS
        up = QGroupBox("⬆  เพิ่มคุณภาพ (Upscale)")
        ul = QVBoxLayout(up)

        ul.addWidget(QLabel("ความละเอียด:"))
        self.res_cb = QComboBox()
        self.res_cb.addItems([
            "Original",
            "1280x720  (720p HD)",
            "1920x1080 (1080p Full HD)",
            "2560x1440 (1440p 2K)",
            "3840x2160 (2160p 4K)",
        ])
        self.res_cb.setCurrentIndex(2)
        ul.addWidget(self.res_cb)

        ul.addWidget(QLabel("เฟรมเรท (FPS):"))
        self.fps_cb = QComboBox()
        self.fps_cb.addItems(["Original", "30", "60", "120"])
        self.fps_cb.setCurrentIndex(2)
        ul.addWidget(self.fps_cb)

        self.interp_cb = QCheckBox("Motion Interpolation (ลื่นกว่า แต่ช้ามาก)")
        self.interp_cb.setToolTip("สร้างเฟรมระหว่างกลางด้วย AI ทำให้นุ่ม\nใช้เวลามาก!")
        ul.addWidget(self.interp_cb)

        ul.addWidget(QLabel("ความคมชัด (Sharpen):"))
        sh_row = QHBoxLayout()
        self.sharp_sl = QSlider(Qt.Horizontal); self.sharp_sl.setRange(0, 15); self.sharp_sl.setValue(5)
        self.sharp_lbl = QLabel("0.5"); self.sharp_lbl.setFixedWidth(30)
        self.sharp_sl.valueChanged.connect(lambda v: self.sharp_lbl.setText(f"{v/10:.1f}"))
        sh_row.addWidget(self.sharp_sl); sh_row.addWidget(self.sharp_lbl)
        ul.addLayout(sh_row)
        rl.addWidget(up)

        # Quality
        q = QGroupBox("⚙  คุณภาพการบีบอัด")
        ql = QVBoxLayout(q)
        cr = QHBoxLayout(); cr.addWidget(QLabel("CRF:"))
        self.crf_sp = QSpinBox(); self.crf_sp.setRange(0,51); self.crf_sp.setValue(17)
        cr.addWidget(self.crf_sp); ql.addLayout(cr)
        self.crf_info = QLabel("17 = คุณภาพสูงมาก"); self.crf_info.setObjectName("info")
        ql.addWidget(self.crf_info)
        self.crf_sp.valueChanged.connect(self._upd_crf)

        ql.addWidget(QLabel("Encoder Preset:"))
        self.preset_cb = QComboBox()
        self.preset_cb.addItems(["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"])
        self.preset_cb.setCurrentText("slow")
        ql.addWidget(self.preset_cb)
        rl.addWidget(q)

        # Export
        exp_btn = QPushButton("🎬  Export MP4")
        exp_btn.setObjectName("success"); exp_btn.setMinimumHeight(48)
        exp_btn.clicked.connect(self._on_export)
        self.export_btn = exp_btn
        rl.addWidget(exp_btn)

        self.status = QLabel("พร้อมใช้งาน")
        self.status.setObjectName("status"); self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        rl.addWidget(self.status)

        self.pbar = QProgressBar(); self.pbar.setRange(0,0); self.pbar.setVisible(False)
        rl.addWidget(self.pbar)
        rl.addStretch()

        sp.addWidget(right)
        sp.setSizes([820, 320])

    # ── handlers ──

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "เลือกไฟล์วิดีโอ", str(Path.home()),
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.flv)"
        )
        if not path: return
        self._video_path = path
        self.file_lbl.setText(Path(path).name)
        self.setWindowTitle(f"Video Upscaler & Editor — {Path(path).name}")
        self._probe(path)
        if VLC_AVAILABLE:
            self.video.load(path)

    def _probe(self, path):
        try:
            r = subprocess.run(
                ["ffprobe","-v","error","-select_streams","v:0",
                 "-show_entries","stream=width,height,r_frame_rate",
                 "-show_entries","format=duration",
                 "-of","default=nw=1", path],
                capture_output=True, text=True
            )
            info = dict(line.split("=",1) for line in r.stdout.strip().split("\n") if "=" in line)
            w = info.get("width","?"); h = info.get("height","?")
            fps_raw = info.get("r_frame_rate","0/1")
            try:
                n,d = fps_raw.split("/"); fps = float(n)/float(d) if float(d)>0 else 0
            except: fps = 0
            dur = float(info.get("duration", 0))
            self._duration = dur
            self._src_info = f"{w}x{h}  {fps:.1f}fps  {fmt_time(dur)}"
            self.file_lbl.setText(f"{Path(path).name}   |   {self._src_info}")
            if not VLC_AVAILABLE:
                self._on_duration(dur)
        except Exception:
            pass

    def _on_duration(self, dur):
        self._duration = dur
        self.start_t.setText("00:00")
        self.end_t.setText(fmt_time(dur))
        self.end_sl.setValue(1000)
        self.status.setText(f"พร้อม Export  •  ต้นฉบับ: {self._src_info}")

    def _update_position(self):
        if not VLC_AVAILABLE or self._seeking: return
        pos = self.video.get_position(); dur = self.video.get_duration() or self._duration
        if dur > 0:
            self.seek_bar.blockSignals(True)
            self.seek_bar.setValue(int(pos/dur*1000))
            self.seek_bar.blockSignals(False)
        self.time_lbl.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")

    def _on_seek(self):
        self._seeking = False
        if self._duration > 0:
            self.video.seek(self.seek_bar.value()/1000.0*self._duration)

    def _set_start(self):
        pos = self.video.get_position() if VLC_AVAILABLE else 0
        self.start_t.setText(fmt_time(pos))
        if self._duration > 0:
            self.start_sl.blockSignals(True)
            self.start_sl.setValue(int(pos/self._duration*1000))
            self.start_sl.blockSignals(False)

    def _set_end(self):
        pos = self.video.get_position() if VLC_AVAILABLE else self._duration
        self.end_t.setText(fmt_time(pos))
        if self._duration > 0:
            self.end_sl.blockSignals(True)
            self.end_sl.setValue(int(pos/self._duration*1000))
            self.end_sl.blockSignals(False)

    def _start_moved(self, v):
        if self._duration > 0: self.start_t.setText(fmt_time(v/1000.0*self._duration))

    def _end_moved(self, v):
        if self._duration > 0: self.end_t.setText(fmt_time(v/1000.0*self._duration))

    def _preview(self):
        if not VLC_AVAILABLE or not self._video_path: return
        start = self.start_sl.value()/1000.0*self._duration
        self.video.seek(start)

    def _upd_crf(self, v):
        if v <= 15: t = f"{v} = ระดับ Lossless (ไฟล์ใหญ่มาก)"
        elif v <= 20: t = f"{v} = คุณภาพสูงมาก (แนะนำ)"
        elif v <= 25: t = f"{v} = คุณภาพสูง"
        elif v <= 30: t = f"{v} = คุณภาพปานกลาง"
        else: t = f"{v} = คุณภาพต่ำ"
        self.crf_info.setText(t)

    def _on_export(self):
        if not self._video_path:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาเปิดไฟล์วิดีโอก่อน"); return

        start = self.start_sl.value()/1000.0*self._duration
        end   = self.end_sl.value()  /1000.0*self._duration
        if end <= start:
            QMessageBox.warning(self, "แจ้งเตือน", "เวลาสิ้นสุดต้องมากกว่าเริ่มต้น"); return

        # parse resolution
        res_txt = self.res_cb.currentText()
        res = res_txt.split()[0] if res_txt != "Original" else ""

        fps = self.fps_cb.currentText()
        if fps == "Original": fps = ""

        default = Path(self._video_path).stem + "_upscaled.mp4"
        out, _ = QFileDialog.getSaveFileName(
            self, "บันทึก MP4",
            str(Path(self._video_path).parent / default), "MP4 (*.mp4)"
        )
        if not out: return

        self.export_btn.setEnabled(False)
        self.pbar.setVisible(True)
        self.status.setText("กำลัง Export...")

        worker = ExportWorker(
            self._video_path, out, start, end,
            self.crf_sp.value(), res, fps,
            self.interp_cb.isChecked(),
            self.sharp_sl.value(),
            self.preset_cb.currentText(),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.status.setText)
        worker.finished.connect(self._exp_done)
        worker.error.connect(self._exp_err)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        thread.start()

    def _exp_done(self, path):
        self.export_btn.setEnabled(True)
        self.pbar.setVisible(False)
        self.status.setText(f"สำเร็จ: {Path(path).name}")
        QMessageBox.information(self, "สำเร็จ", f"บันทึกเสร็จแล้ว:\n{path}")

    def _exp_err(self, msg):
        self.export_btn.setEnabled(True)
        self.pbar.setVisible(False)
        self.status.setText("Error")
        QMessageBox.critical(self, "เกิดข้อผิดพลาด", msg)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow(); win.show()
    sys.exit(app.exec_())
