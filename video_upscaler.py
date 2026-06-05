#!/usr/bin/env python3
"""Video Upscaler & Batch Editor — ตั้งค่าครั้งเดียว export ทีละหลายไฟล์"""

import os
import sys
import time
import tempfile
import subprocess
import threading
from pathlib import Path

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QLabel, QSlider, QComboBox, QFileDialog,
        QProgressBar, QMessageBox, QSpinBox, QGroupBox, QCheckBox,
        QListWidget, QListWidgetItem, QAbstractItemView, QSplitter,
        QFrame
    )
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
    from PyQt5.QtGui import QColor
except ImportError:
    print("กรุณาติดตั้ง PyQt5: pip install PyQt5"); sys.exit(1)

try:
    import vlc
    VLC_AVAILABLE = True
except Exception:
    VLC_AVAILABLE = False


# ──────────────────────────────────────────────
# Batch Export Worker
# ──────────────────────────────────────────────

class BatchWorker(QObject):
    file_started  = pyqtSignal(int, str)   # index, filename
    file_progress = pyqtSignal(int, str)   # percent, status text
    file_done     = pyqtSignal(int)        # index
    file_error    = pyqtSignal(int, str)   # index, error msg
    all_done      = pyqtSignal()

    LOG_PATH = str(Path.home() / "export_log.txt")

    def __init__(self, jobs, settings):
        super().__init__()
        # jobs: list of {"src", "start", "end"}
        # settings: dict with crf, res, fps, interp, sharpen, preset, mute, out_dir
        self.jobs     = jobs
        self.settings = settings
        self._stop    = False

    def stop(self):
        self._stop = True

    def _log(self, msg):
        try:
            with open(self.LOG_PATH, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception: pass

    def run(self):
        s = self.settings
        for i, job in enumerate(self.jobs):
            if self._stop:
                break
            src   = job["src"]
            start = job["start"]
            end   = job["end"]
            name  = Path(src).stem + "_export.mp4"
            dst   = str(Path(s["out_dir"]) / name)
            # avoid overwrite
            base = Path(dst)
            k = 1
            while base.exists():
                base = Path(s["out_dir"]) / f"{Path(src).stem}_export_{k}.mp4"
                k += 1
            dst = str(base)

            self.file_started.emit(i, Path(src).name)
            self._log(f"\n=== [{i+1}/{len(self.jobs)}] {Path(src).name} ===")
            self._export_one(i, src, dst, start, end)

        self.all_done.emit()

    def _export_one(self, idx, src, dst, start, end):
        s = self.settings
        total = max(0.001, end - start)

        cmd = ["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", src]

        vf = []
        if s["res"] and s["res"] != "Original":
            w, h = s["res"].split("x")
            vf.append(f"scale={w}:{h}:flags=lanczos")
        if s["sharpen"] > 0:
            vf.append(f"unsharp=5:5:{s['sharpen']/10:.1f}:5:5:0.0")
        if s["fps"] and s["fps"] != "Original":
            if s["interp"]:
                vf.append(f"minterpolate=fps={s['fps']}:mi_mode=mci:mc_mode=aobmc:vsbmf=1")
            else:
                vf.append(f"fps={s['fps']}")
        if vf:
            cmd += ["-vf", ",".join(vf)]

        cmd += ["-c:v", "libx264", "-crf", str(s["crf"]),
                "-preset", s["preset"], "-pix_fmt", "yuv420p"]

        if s["mute"]:
            cmd += ["-an"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k"]

        prog_fd, prog_path = tempfile.mkstemp(suffix=".txt", prefix="ffprog_")
        os.close(prog_fd)
        cmd += ["-progress", prog_path, "-nostats", dst]

        self._log("cmd: " + " ".join(f'"{c}"' if " " in c else c for c in cmd))

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            self.file_error.emit(idx, "ไม่พบ ffmpeg — ติดตั้งจาก ffmpeg.org")
            return

        err_buf = []
        def _drain():
            try:
                for ln in iter(proc.stderr.readline, b""):
                    err_buf.append(ln.decode(errors="ignore"))
                    if len(err_buf) > 200: err_buf.pop(0)
            except Exception: pass
        threading.Thread(target=_drain, daemon=True).start()

        last_pct = -1
        speed_str = ""
        while proc.poll() is None and not self._stop:
            time.sleep(0.4)
            try:
                with open(prog_path, "r", errors="ignore") as f:
                    data = f.read()
            except Exception: continue
            if not data: continue
            kv = {}
            for ln in data.strip().splitlines():
                if "=" in ln:
                    k, v = ln.split("=", 1); kv[k.strip()] = v.strip()
            if "speed" in kv: speed_str = kv["speed"]
            t_us = kv.get("out_time_us") or kv.get("out_time_ms")
            if t_us and t_us.lstrip("-").isdigit() and int(t_us) > 0:
                cur = int(t_us) / 1_000_000.0
                pct = min(99, int(cur / total * 100))
                if pct != last_pct:
                    last_pct = pct
                    self.file_progress.emit(pct, f"{pct}%  {speed_str}")
                    self._log(f"  -> {pct}%  speed={speed_str}")

        if self._stop:
            proc.kill()

        rc = proc.wait()
        try: os.remove(prog_path)
        except: pass

        if rc != 0:
            err = "".join(err_buf)[-600:]
            self._log(f"ERROR rc={rc}: {err}")
            self.file_error.emit(idx, err or f"ffmpeg rc={rc}")
        else:
            self._log(f"Done: {dst}")
            self.file_progress.emit(100, "100%")
            self.file_done.emit(idx)


# ──────────────────────────────────────────────
# VLC Player Widget
# ──────────────────────────────────────────────

class VideoWidget(QWidget):
    duration_ready = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(300)
        self.setStyleSheet("background: #0a0a0a;")
        self._instance = self._player = self._media = None
        self._duration = 0.0
        self._dur_emitted = False

        if VLC_AVAILABLE:
            vlc_args = ["--no-xlib", "--quiet", "--avcodec-hw=none", "--no-video-title-show"]
            if sys.platform == "win32":
                vlc_args += ["--vout=direct3d9"]
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
        self._label.setStyleSheet("color:#555; font-size:14px;")
        self._label.setText("เลือกไฟล์ด้านซ้ายเพื่อ Preview")

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
QMainWindow, QWidget { background:#1a1a2e; color:#e0e0e0; font-family:'Segoe UI'; font-size:13px; }
QGroupBox {
    border:1px solid #2d2d4e; border-radius:8px;
    margin-top:10px; padding:8px; font-weight:bold; color:#9d8fff;
}
QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }
QPushButton {
    background:#2d2d4e; color:#e0e0e0; border:none;
    border-radius:6px; padding:7px 14px;
}
QPushButton:hover  { background:#3d3d6e; }
QPushButton:pressed { background:#5a4fff; }
QPushButton#primary { background:#5a4fff; font-weight:bold; padding:9px 18px; }
QPushButton#primary:hover { background:#7a6fff; }
QPushButton#success { background:#1a7a3a; font-weight:bold; font-size:14px; }
QPushButton#success:hover { background:#229547; }
QPushButton#danger { background:#7a1a1a; }
QPushButton#danger:hover { background:#9a2222; }
QComboBox, QSpinBox {
    background:#0d0d1a; border:1px solid #3d3d6e; border-radius:6px;
    padding:5px 8px; color:#e0e0e0;
}
QListWidget {
    background:#0d0d1a; border:1px solid #3d3d6e; border-radius:6px;
    color:#e0e0e0; alternate-background-color:#111130;
}
QListWidget::item:selected { background:#3d3d6e; }
QSlider::groove:horizontal { height:4px; background:#2d2d4e; border-radius:2px; }
QSlider::sub-page:horizontal { background:#5a4fff; border-radius:2px; }
QSlider::handle:horizontal {
    background:#fff; border:2px solid #5a4fff;
    width:14px; height:14px; margin:-5px 0; border-radius:7px;
}
QProgressBar {
    background:#0d0d1a; border:1px solid #3d3d6e; border-radius:4px;
    height:14px; text-align:center; color:#fff; font-size:11px;
}
QProgressBar::chunk { background:#5a4fff; border-radius:4px; }
QLabel#status { color:#9d8fff; font-size:12px; }
QLabel#time   { color:#aaa; font-family:'Consolas',monospace; }
QLabel#info   { color:#888; font-size:11px; }
QCheckBox { color:#ccc; }
QFrame#sep { background:#2d2d4e; }
"""


def fmt_time(s):
    s = max(0, int(s))
    h, r = divmod(s, 3600)
    m, s2 = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s2:02d}" if h else f"{m:02d}:{s2:02d}"


def probe_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe","-v","error","-select_streams","v:0",
             "-show_entries","stream=width,height,r_frame_rate",
             "-show_entries","format=duration",
             "-of","default=nw=1", path],
            capture_output=True, text=True
        )
        kv = {}
        for ln in r.stdout.strip().splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1); kv[k] = v
        dur = float(kv.get("duration", 0))
        w   = kv.get("width", "?")
        h   = kv.get("height", "?")
        fr  = kv.get("r_frame_rate", "0/1")
        try:
            n, d = fr.split("/"); fps = float(n)/float(d) if float(d) else 0
        except: fps = 0
        return dur, f"{w}x{h}  {fps:.0f}fps"
    except Exception:
        return 0.0, ""


# ──────────────────────────────────────────────
# Main Window
# ──────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Upscaler & Batch Editor")
        self.resize(1300, 800)
        self.setStyleSheet(DARK)

        self._jobs      = []   # list of {"src","start","end","dur","info","item"}
        self._cur_idx   = -1
        self._out_dir   = ""
        self._seeking   = False
        self._thread    = None
        self._worker    = None

        self._build_ui()

        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._update_pos)
        self._pos_timer.start(300)

    # ── Build UI ──────────────────────────────

    def _build_ui(self):
        cw = QWidget(); self.setCentralWidget(cw)
        root = QHBoxLayout(cw)
        root.setContentsMargins(10,10,10,10); root.setSpacing(8)

        # ── LEFT: file list ──
        left = QWidget(); left.setFixedWidth(300)
        ll = QVBoxLayout(left); ll.setContentsMargins(0,0,0,0); ll.setSpacing(6)

        list_box = QGroupBox("📋 รายการวิดีโอ")
        lb_lay = QVBoxLayout(list_box)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("➕ เพิ่มไฟล์")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add_files)
        rm_btn = QPushButton("🗑 ลบ")
        rm_btn.setObjectName("danger")
        rm_btn.clicked.connect(self._remove_selected)
        clear_btn = QPushButton("✖ ล้าง")
        clear_btn.clicked.connect(self._clear_list)
        btn_row.addWidget(add_btn); btn_row.addWidget(rm_btn); btn_row.addWidget(clear_btn)
        lb_lay.addLayout(btn_row)

        self.file_list = QListWidget()
        self.file_list.setAlternatingRowColors(True)
        self.file_list.itemClicked.connect(self._on_item_click)
        lb_lay.addWidget(self.file_list)

        self.list_info = QLabel("0 ไฟล์")
        self.list_info.setObjectName("info")
        lb_lay.addWidget(self.list_info)

        ll.addWidget(list_box)

        # output folder
        out_box = QGroupBox("📁 โฟลเดอร์ Output")
        out_lay = QVBoxLayout(out_box)
        self.out_lbl = QLabel("ยังไม่ได้เลือก")
        self.out_lbl.setObjectName("info")
        self.out_lbl.setWordWrap(True)
        out_lay.addWidget(self.out_lbl)
        pick_btn = QPushButton("📂 เลือกโฟลเดอร์")
        pick_btn.setObjectName("primary")
        pick_btn.clicked.connect(self._pick_out_dir)
        out_lay.addWidget(pick_btn)
        ll.addWidget(out_box)

        root.addWidget(left)

        # ── CENTER: preview ──
        center = QWidget()
        cl = QVBoxLayout(center); cl.setContentsMargins(0,0,0,0); cl.setSpacing(6)

        self.video = VideoWidget()
        self.video.duration_ready.connect(self._on_dur)
        cl.addWidget(self.video, stretch=1)

        self.seek_bar = QSlider(Qt.Horizontal)
        self.seek_bar.setRange(0,1000)
        self.seek_bar.sliderPressed.connect(lambda: setattr(self,"_seeking",True))
        self.seek_bar.sliderReleased.connect(self._on_seek)
        cl.addWidget(self.seek_bar)

        tp = QHBoxLayout()
        self.time_lbl = QLabel("00:00 / 00:00"); self.time_lbl.setObjectName("time")
        self.play_btn = QPushButton("▶  Play / Pause")
        self.play_btn.clicked.connect(self.video.play_pause)
        vol = QSlider(Qt.Horizontal); vol.setRange(0,100); vol.setValue(80); vol.setFixedWidth(90)
        vol.valueChanged.connect(self.video.set_volume)
        tp.addWidget(self.time_lbl); tp.addStretch()
        tp.addWidget(self.play_btn); tp.addWidget(QLabel("🔊")); tp.addWidget(vol)
        cl.addLayout(tp)

        # trim per-file
        trim_box = QGroupBox("✂  ตัดต่อ (สำหรับไฟล์ที่เลือก)")
        tl = QVBoxLayout(trim_box)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("เริ่ม:"))
        self.start_t = QLabel("00:00"); self.start_t.setObjectName("time")
        r1.addWidget(self.start_t); r1.addStretch()
        sb = QPushButton("📍 Set เริ่ม"); sb.clicked.connect(self._set_start); r1.addWidget(sb)
        tl.addLayout(r1)
        self.start_sl = QSlider(Qt.Horizontal); self.start_sl.setRange(0,1000)
        self.start_sl.valueChanged.connect(self._start_moved); tl.addWidget(self.start_sl)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("สิ้นสุด:"))
        self.end_t = QLabel("00:00"); self.end_t.setObjectName("time")
        r2.addWidget(self.end_t); r2.addStretch()
        eb = QPushButton("📍 Set สิ้นสุด"); eb.clicked.connect(self._set_end); r2.addWidget(eb)
        tl.addLayout(r2)
        self.end_sl = QSlider(Qt.Horizontal); self.end_sl.setRange(0,1000); self.end_sl.setValue(1000)
        self.end_sl.valueChanged.connect(self._end_moved); tl.addWidget(self.end_sl)
        save_trim = QPushButton("💾 บันทึก Trim สำหรับไฟล์นี้")
        save_trim.clicked.connect(self._save_trim); tl.addWidget(save_trim)
        cl.addWidget(trim_box)

        root.addWidget(center, stretch=1)

        # ── RIGHT: settings + export ──
        right = QWidget(); right.setFixedWidth(300)
        rl = QVBoxLayout(right); rl.setContentsMargins(0,0,0,0); rl.setSpacing(8)

        # upscale
        up = QGroupBox("⬆  เพิ่มคุณภาพ (ใช้กับทุกไฟล์)")
        ul = QVBoxLayout(up)
        ul.addWidget(QLabel("ความละเอียด:"))
        self.res_cb = QComboBox()
        self.res_cb.addItems(["Original","1280x720  (720p)","1920x1080 (1080p)","2560x1440 (2K)","3840x2160 (4K)"])
        self.res_cb.setCurrentIndex(2)
        ul.addWidget(self.res_cb)
        ul.addWidget(QLabel("เฟรมเรท (FPS):"))
        self.fps_cb = QComboBox()
        self.fps_cb.addItems(["Original","30","60","120"])
        self.fps_cb.setCurrentIndex(2)
        ul.addWidget(self.fps_cb)
        self.interp_cb = QCheckBox("Motion Interpolation (ช้า แต่นุ่มกว่า)")
        ul.addWidget(self.interp_cb)
        ul.addWidget(QLabel("Sharpen:"))
        sh = QHBoxLayout()
        self.sharp_sl = QSlider(Qt.Horizontal); self.sharp_sl.setRange(0,15); self.sharp_sl.setValue(3)
        self.sharp_lbl = QLabel("0.3")
        self.sharp_sl.valueChanged.connect(lambda v: self.sharp_lbl.setText(f"{v/10:.1f}"))
        sh.addWidget(self.sharp_sl); sh.addWidget(self.sharp_lbl); ul.addLayout(sh)
        rl.addWidget(up)

        # quality
        q = QGroupBox("⚙  คุณภาพการบีบอัด")
        ql = QVBoxLayout(q)
        cr = QHBoxLayout(); cr.addWidget(QLabel("CRF:"))
        self.crf_sp = QSpinBox(); self.crf_sp.setRange(0,51); self.crf_sp.setValue(18)
        cr.addWidget(self.crf_sp); ql.addLayout(cr)
        self.crf_info = QLabel("18 = คุณภาพสูงมาก"); self.crf_info.setObjectName("info")
        self.crf_sp.valueChanged.connect(self._upd_crf); ql.addWidget(self.crf_info)
        ql.addWidget(QLabel("Encoder Preset:"))
        self.preset_cb = QComboBox()
        self.preset_cb.addItems(["ultrafast","superfast","veryfast","faster","fast","medium","slow","veryslow"])
        self.preset_cb.setCurrentText("medium")
        ql.addWidget(self.preset_cb)
        rl.addWidget(q)

        # audio
        aud = QGroupBox("🔇  เสียง")
        al = QVBoxLayout(aud)
        self.mute_cb = QCheckBox("ตัดเสียงออก (ไม่มีเสียงใน MP4)")
        self.mute_cb.setChecked(True)   # default ON สำหรับ Shopee
        al.addWidget(self.mute_cb)
        note = QLabel("✅ แนะนำสำหรับวิดีโอ Shopee\n(ใส่เพลงเองในแอป Shopee)")
        note.setObjectName("info"); note.setWordWrap(True); al.addWidget(note)
        rl.addWidget(aud)

        # batch progress
        bp = QGroupBox("📊  Progress")
        bpl = QVBoxLayout(bp)
        self.batch_lbl = QLabel("รอ Export"); self.batch_lbl.setObjectName("status")
        bpl.addWidget(self.batch_lbl)
        self.batch_bar = QProgressBar(); self.batch_bar.setRange(0,100); self.batch_bar.setValue(0)
        self.batch_bar.setFormat("ไฟล์ %p%")
        bpl.addWidget(self.batch_bar)
        self.total_bar = QProgressBar(); self.total_bar.setRange(0,100); self.total_bar.setValue(0)
        self.total_bar.setFormat("รวม %p%")
        bpl.addWidget(self.total_bar)
        rl.addWidget(bp)

        # export button
        self.export_btn = QPushButton("🎬  Export ทุกไฟล์")
        self.export_btn.setObjectName("success")
        self.export_btn.setMinimumHeight(50)
        self.export_btn.clicked.connect(self._start_batch)
        rl.addWidget(self.export_btn)

        self.stop_btn = QPushButton("⏹  หยุด")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_batch)
        rl.addWidget(self.stop_btn)

        rl.addStretch()
        root.addWidget(right)

    # ── File list ─────────────────────────────

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "เลือกไฟล์วิดีโอ", str(Path.home()),
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.flv)"
        )
        for p in paths:
            if any(j["src"] == p for j in self._jobs):
                continue
            dur, info = probe_duration(p)
            item = QListWidgetItem(f"⏳ {Path(p).name}")
            item.setToolTip(f"{p}\n{info}  {fmt_time(dur)}")
            self.file_list.addItem(item)
            self._jobs.append({"src":p,"start":0,"end":dur,"dur":dur,"info":info,"item":item})
            item.setText(f"✔ {Path(p).name}")
        self._upd_list_info()

    def _remove_selected(self):
        for item in self.file_list.selectedItems():
            row = self.file_list.row(item)
            self.file_list.takeItem(row)
            self._jobs.pop(row)
        self._upd_list_info()

    def _clear_list(self):
        self.file_list.clear(); self._jobs.clear()
        self._cur_idx = -1; self._upd_list_info()

    def _upd_list_info(self):
        n = len(self._jobs)
        self.list_info.setText(f"{n} ไฟล์  รวม {sum(j['end']-j['start'] for j in self._jobs):.0f}s")

    def _on_item_click(self, item):
        row = self.file_list.row(item)
        if row < 0 or row >= len(self._jobs): return
        self._cur_idx = row
        job = self._jobs[row]
        # update trim sliders
        dur = job["dur"] or 1
        self.start_sl.blockSignals(True); self.end_sl.blockSignals(True)
        self.start_sl.setValue(int(job["start"]/dur*1000))
        self.end_sl.setValue(int(job["end"]/dur*1000))
        self.start_t.setText(fmt_time(job["start"]))
        self.end_t.setText(fmt_time(job["end"]))
        self.start_sl.blockSignals(False); self.end_sl.blockSignals(False)
        # load video
        if VLC_AVAILABLE:
            self.video.load(job["src"])
        else:
            self._on_dur(job["dur"])

    def _pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์ Output", str(Path.home()))
        if d:
            self._out_dir = d
            self.out_lbl.setText(d)

    # ── Trim ──────────────────────────────────

    def _cur_job(self):
        if 0 <= self._cur_idx < len(self._jobs):
            return self._jobs[self._cur_idx]
        return None

    def _on_dur(self, dur):
        j = self._cur_job()
        if j: j["dur"] = dur

    def _set_start(self):
        pos = self.video.get_position() if VLC_AVAILABLE else 0
        j = self._cur_job();
        if not j: return
        j["start"] = pos; dur = j["dur"] or 1
        self.start_t.setText(fmt_time(pos))
        self.start_sl.blockSignals(True)
        self.start_sl.setValue(int(pos/dur*1000))
        self.start_sl.blockSignals(False)
        self._upd_list_info()

    def _set_end(self):
        pos = self.video.get_position() if VLC_AVAILABLE else self._cur_job()["dur"]
        j = self._cur_job();
        if not j: return
        j["end"] = pos; dur = j["dur"] or 1
        self.end_t.setText(fmt_time(pos))
        self.end_sl.blockSignals(True)
        self.end_sl.setValue(int(pos/dur*1000))
        self.end_sl.blockSignals(False)
        self._upd_list_info()

    def _start_moved(self, v):
        j = self._cur_job()
        if j and j["dur"]:
            t = v/1000.0*j["dur"]; j["start"] = t
            self.start_t.setText(fmt_time(t))

    def _end_moved(self, v):
        j = self._cur_job()
        if j and j["dur"]:
            t = v/1000.0*j["dur"]; j["end"] = t
            self.end_t.setText(fmt_time(t))

    def _save_trim(self):
        j = self._cur_job()
        if not j: return
        name = Path(j["src"]).name
        j["item"].setText(f"✔ {name}  [{fmt_time(j['start'])}→{fmt_time(j['end'])}]")
        self._upd_list_info()
        self.batch_lbl.setText(f"บันทึก Trim: {name}")

    def _update_pos(self):
        if not VLC_AVAILABLE or self._seeking: return
        pos = self.video.get_position()
        j   = self._cur_job()
        dur = self.video.get_duration() or (j["dur"] if j else 0)
        if dur > 0:
            self.seek_bar.blockSignals(True)
            self.seek_bar.setValue(int(pos/dur*1000))
            self.seek_bar.blockSignals(False)
        self.time_lbl.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")

    def _on_seek(self):
        self._seeking = False
        j = self._cur_job()
        if j and j["dur"] > 0:
            self.video.seek(self.seek_bar.value()/1000.0*j["dur"])

    def _upd_crf(self, v):
        labels = {0:"Lossless (ใหญ่มาก)", 15:"คุณภาพสูงสุด",
                  18:"คุณภาพสูงมาก (แนะนำ)", 23:"คุณภาพสูง",
                  28:"ปานกลาง", 35:"ต่ำ"}
        for threshold, txt in sorted(labels.items(), reverse=True):
            if v >= threshold:
                self.crf_info.setText(f"{v} = {txt}"); break

    # ── Batch Export ──────────────────────────

    def _start_batch(self):
        if not self._jobs:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาเพิ่มไฟล์วิดีโอก่อน"); return
        if not self._out_dir:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาเลือกโฟลเดอร์ Output ก่อน"); return

        # validate
        bad = [j for j in self._jobs if j["end"] <= j["start"]]
        if bad:
            names = "\n".join(Path(j["src"]).name for j in bad[:5])
            QMessageBox.warning(self, "Trim ผิด", f"ไฟล์ต่อไปนี้ end ≤ start:\n{names}"); return

        res_txt = self.res_cb.currentText(); res = res_txt.split()[0] if res_txt != "Original" else ""
        fps = self.fps_cb.currentText(); fps = "" if fps == "Original" else fps
        settings = {
            "crf":     self.crf_sp.value(),
            "res":     res,
            "fps":     fps,
            "interp":  self.interp_cb.isChecked(),
            "sharpen": self.sharp_sl.value(),
            "preset":  self.preset_cb.currentText(),
            "mute":    self.mute_cb.isChecked(),
            "out_dir": self._out_dir,
        }

        # reset UI
        for j in self._jobs:
            j["item"].setForeground(QColor("#e0e0e0"))
        self.total_bar.setValue(0)
        self.batch_bar.setValue(0)
        self.export_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        jobs_data = [{"src":j["src"],"start":j["start"],"end":j["end"]} for j in self._jobs]
        worker = BatchWorker(jobs_data, settings)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.file_started.connect(self._on_file_started)
        worker.file_progress.connect(self._on_file_progress)
        worker.file_done.connect(self._on_file_done)
        worker.file_error.connect(self._on_file_error)
        worker.all_done.connect(self._on_all_done)
        worker.all_done.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread; self._worker = worker
        thread.start()

    def _stop_batch(self):
        if self._worker: self._worker.stop()
        self.stop_btn.setEnabled(False)
        self.batch_lbl.setText("กำลังหยุด...")

    def _on_file_started(self, idx, name):
        self.batch_lbl.setText(f"[{idx+1}/{len(self._jobs)}] {name}")
        self.batch_bar.setValue(0)
        item = self._jobs[idx]["item"]
        item.setForeground(QColor("#ffcc00"))
        item.setText(f"⏳ {Path(self._jobs[idx]['src']).name}")
        self.file_list.scrollToItem(item)

    def _on_file_progress(self, pct, txt):
        self.batch_bar.setValue(pct)
        cur = self.file_list.currentRow()
        total_pct = 0
        if self._jobs:
            done = next((i for i,j in enumerate(self._jobs) if j["item"].foreground().color().name() == "#ffcc00"), 0)
            total_pct = int((done + pct/100.0) / len(self._jobs) * 100)
        self.total_bar.setValue(total_pct)
        self.batch_lbl.setText(txt)

    def _on_file_done(self, idx):
        item = self._jobs[idx]["item"]
        item.setForeground(QColor("#4caf50"))
        item.setText(f"✅ {Path(self._jobs[idx]['src']).name}")
        done = sum(1 for j in self._jobs if "✅" in j["item"].text())
        self.total_bar.setValue(int(done/len(self._jobs)*100))

    def _on_file_error(self, idx, msg):
        item = self._jobs[idx]["item"]
        item.setForeground(QColor("#f44336"))
        item.setText(f"❌ {Path(self._jobs[idx]['src']).name}")
        self.batch_lbl.setText(f"Error: {msg[:60]}")

    def _on_all_done(self):
        self.export_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        ok = sum(1 for j in self._jobs if "✅" in j["item"].text())
        fail = sum(1 for j in self._jobs if "❌" in j["item"].text())
        self.batch_lbl.setText(f"เสร็จแล้ว ✅{ok} ❌{fail}")
        self.total_bar.setValue(100)
        QMessageBox.information(self, "เสร็จสิ้น",
            f"Export เสร็จแล้ว!\n✅ สำเร็จ: {ok} ไฟล์\n❌ ผิดพลาด: {fail} ไฟล์\n\nบันทึกไปที่:\n{self._out_dir}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow(); win.show()
    sys.exit(app.exec_())
