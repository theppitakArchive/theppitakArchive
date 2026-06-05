#!/usr/bin/env python3
"""YouTube Video Downloader & Editor - ดาวน์โหลด ตัดต่อ และส่งออก MP4"""

import os
import sys
import argparse
import subprocess
from pathlib import Path

try:
    import yt_dlp
    from moviepy import VideoFileClip, concatenate_videoclips
except ImportError as e:
    print(f"ขาด library: {e}")
    print("รัน: pip install yt-dlp moviepy")
    sys.exit(1)


def download_video(url: str, output_dir: str = "downloads", quality: str = "best") -> str:
    """ดาวน์โหลด YouTube video และคืน path ของไฟล์"""
    os.makedirs(output_dir, exist_ok=True)

    quality_map = {
        "360p":  "bestvideo[height<=360]+bestaudio/best[height<=360]",
        "480p":  "bestvideo[height<=480]+bestaudio/best[height<=480]",
        "720p":  "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "best":  "bestvideo+bestaudio/best",
    }
    format_str = quality_map.get(quality, quality_map["best"])

    ydl_opts = {
        "format": format_str,
        "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": False,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        # yt-dlp อาจเปลี่ยนนามสกุลเป็น .mp4 หลัง merge
        if not Path(filename).exists():
            filename = str(Path(filename).with_suffix(".mp4"))
        print(f"\n[✓] ดาวน์โหลดสำเร็จ: {filename}")
        return filename


def trim_video(input_path: str, start: float, end: float, output_path: str | None = None) -> str:
    """ตัดวิดีโอตาม start/end (วินาที)"""
    if output_path is None:
        p = Path(input_path)
        output_path = str(p.with_stem(p.stem + f"_trim_{int(start)}-{int(end)}"))

    clip = VideoFileClip(input_path).subclipped(start, end)
    clip.write_videofile(output_path, codec="libx264", audio_codec="aac", logger="bar")
    clip.close()
    print(f"[✓] ตัดวิดีโอเสร็จ: {output_path}")
    return output_path


def concat_videos(input_paths: list[str], output_path: str) -> str:
    """ต่อวิดีโอหลายไฟล์เข้าด้วยกัน"""
    clips = [VideoFileClip(p) for p in input_paths]
    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(output_path, codec="libx264", audio_codec="aac", logger="bar")
    for c in clips:
        c.close()
    final.close()
    print(f"[✓] ต่อวิดีโอเสร็จ: {output_path}")
    return output_path


def adjust_quality(input_path: str, output_path: str | None = None,
                   crf: int = 23, resolution: str | None = None,
                   bitrate: str | None = None) -> str:
    """
    ปรับคุณภาพวิดีโอด้วย ffmpeg
    crf: 0 (ดีที่สุด) - 51 (แย่ที่สุด), ค่าเริ่มต้น 23
    resolution: เช่น '1280x720', '1920x1080'
    bitrate: เช่น '2M', '5M'
    """
    if output_path is None:
        p = Path(input_path)
        output_path = str(p.with_stem(p.stem + "_adjusted"))

    cmd = ["ffmpeg", "-y", "-i", input_path]

    vf_filters = []
    if resolution:
        w, h = resolution.split("x")
        vf_filters.append(f"scale={w}:{h}")

    if vf_filters:
        cmd += ["-vf", ",".join(vf_filters)]

    cmd += ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium"]

    if bitrate:
        cmd += ["-b:v", bitrate]

    cmd += ["-c:a", "aac", "-b:a", "128k", output_path]

    print(f"[→] รัน ffmpeg: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        raise RuntimeError("ffmpeg ล้มเหลว")

    print(f"[✓] ปรับคุณภาพเสร็จ: {output_path}")
    return output_path


def interactive_menu():
    """เมนูโต้ตอบแบบ CLI"""
    print("\n" + "="*55)
    print("  YouTube Video Downloader & Editor")
    print("="*55)
    print("1. ดาวน์โหลด YouTube วิดีโอ")
    print("2. ตัดวิดีโอ (Trim)")
    print("3. ต่อวิดีโอหลายไฟล์ (Concatenate)")
    print("4. ปรับคุณภาพ / ความคมชัด (Quality)")
    print("5. ดาวน์โหลด + ตัด + ปรับคุณภาพ (All-in-one)")
    print("0. ออก")
    print("-"*55)

    choice = input("เลือก: ").strip()

    if choice == "1":
        url = input("URL YouTube: ").strip()
        print("คุณภาพ: 360p / 480p / 720p / 1080p / best")
        quality = input("คุณภาพ [best]: ").strip() or "best"
        download_video(url, quality=quality)

    elif choice == "2":
        path = input("Path ไฟล์วิดีโอ: ").strip()
        start = float(input("เริ่ม (วินาที): "))
        end = float(input("สิ้นสุด (วินาที): "))
        out = input("Output path [Enter=อัตโนมัติ]: ").strip() or None
        trim_video(path, start, end, out)

    elif choice == "3":
        print("ป้อน path ไฟล์วิดีโอ (Enter ว่างเพื่อหยุด):")
        paths = []
        while True:
            p = input(f"  ไฟล์ {len(paths)+1}: ").strip()
            if not p:
                break
            paths.append(p)
        if len(paths) < 2:
            print("[!] ต้องการอย่างน้อย 2 ไฟล์")
            return
        out = input("Output path: ").strip()
        concat_videos(paths, out)

    elif choice == "4":
        path = input("Path ไฟล์วิดีโอ: ").strip()
        out = input("Output path [Enter=อัตโนมัติ]: ").strip() or None
        print("CRF: 0=ดีที่สุด, 23=ปกติ, 51=แย่ที่สุด")
        crf = int(input("CRF [23]: ").strip() or "23")
        res = input("ความละเอียด เช่น 1280x720 [Enter=เดิม]: ").strip() or None
        btr = input("Bitrate เช่น 2M [Enter=อัตโนมัติ]: ").strip() or None
        adjust_quality(path, out, crf=crf, resolution=res, bitrate=btr)

    elif choice == "5":
        url = input("URL YouTube: ").strip()
        print("คุณภาพดาวน์โหลด: 360p / 480p / 720p / 1080p / best")
        quality = input("คุณภาพ [best]: ").strip() or "best"

        do_trim = input("ต้องการตัดวิดีโอ? (y/n) [n]: ").strip().lower() == "y"
        start = end = None
        if do_trim:
            start = float(input("  เริ่ม (วินาที): "))
            end = float(input("  สิ้นสุด (วินาที): "))

        print("ปรับคุณภาพ Output:")
        crf = int(input("  CRF [23]: ").strip() or "23")
        res = input("  ความละเอียด เช่น 1280x720 [Enter=เดิม]: ").strip() or None
        btr = input("  Bitrate เช่น 2M [Enter=อัตโนมัติ]: ").strip() or None

        # ดาวน์โหลด
        downloaded = download_video(url, quality=quality)

        # ตัด (ถ้าต้องการ)
        current = downloaded
        if do_trim and start is not None and end is not None:
            current = trim_video(current, start, end)

        # ปรับคุณภาพ
        final = adjust_quality(current, crf=crf, resolution=res, bitrate=btr)
        print(f"\n[✓] ไฟล์ Final: {final}")

    elif choice == "0":
        print("ออกโปรแกรม")
    else:
        print("[!] ไม่รู้จักตัวเลือกนี้")


def main():
    parser = argparse.ArgumentParser(
        description="YouTube Video Downloader & Editor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ตัวอย่าง:
  # รันแบบเมนูโต้ตอบ
  python youtube_editor.py

  # ดาวน์โหลด 720p
  python youtube_editor.py download -u URL -q 720p

  # ตัดวิดีโอ
  python youtube_editor.py trim -i input.mp4 -s 10 -e 60

  # ปรับคุณภาพ CRF=18 (คมชัดสูง), ความละเอียด 1280x720
  python youtube_editor.py quality -i input.mp4 --crf 18 -r 1280x720

  # ต่อวิดีโอ
  python youtube_editor.py concat -i a.mp4 b.mp4 c.mp4 -o output.mp4
        """
    )

    subparsers = parser.add_subparsers(dest="command")

    # download
    dl = subparsers.add_parser("download", help="ดาวน์โหลด YouTube video")
    dl.add_argument("-u", "--url", required=True)
    dl.add_argument("-q", "--quality", default="best",
                    choices=["360p", "480p", "720p", "1080p", "best"])
    dl.add_argument("-o", "--output-dir", default="downloads")

    # trim
    tr = subparsers.add_parser("trim", help="ตัดวิดีโอ")
    tr.add_argument("-i", "--input", required=True)
    tr.add_argument("-s", "--start", type=float, required=True)
    tr.add_argument("-e", "--end", type=float, required=True)
    tr.add_argument("-o", "--output")

    # concat
    cc = subparsers.add_parser("concat", help="ต่อวิดีโอหลายไฟล์")
    cc.add_argument("-i", "--inputs", nargs="+", required=True)
    cc.add_argument("-o", "--output", required=True)

    # quality
    ql = subparsers.add_parser("quality", help="ปรับคุณภาพ/ความคมชัด")
    ql.add_argument("-i", "--input", required=True)
    ql.add_argument("-o", "--output")
    ql.add_argument("--crf", type=int, default=23,
                    help="0=ดีที่สุด, 23=ปกติ, 51=แย่ที่สุด")
    ql.add_argument("-r", "--resolution", help="เช่น 1280x720")
    ql.add_argument("-b", "--bitrate", help="เช่น 2M, 5M")

    args = parser.parse_args()

    if args.command == "download":
        download_video(args.url, args.output_dir, args.quality)
    elif args.command == "trim":
        trim_video(args.input, args.start, args.end, args.output)
    elif args.command == "concat":
        concat_videos(args.inputs, args.output)
    elif args.command == "quality":
        adjust_quality(args.input, args.output, args.crf, args.resolution, args.bitrate)
    else:
        interactive_menu()


if __name__ == "__main__":
    main()
