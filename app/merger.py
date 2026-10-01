"""Ghép các clip thành 1 video bằng ffmpeg (re-encode để tránh lệch codec/timebase)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg

RUN = dict(capture_output=True, text=True, encoding="utf-8", errors="replace",
           **({"creationflags": 0x08000000} if sys.platform == "win32" else {}))   # CREATE_NO_WINDOW


def _quote(path: Path) -> str:
    return "file '" + path.resolve().as_posix().replace("'", "'\\''") + "'\n"


def merge(clips: list[Path], output: Path) -> Path:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.writelines(_quote(c) for c in clips)
        listfile = f.name
    output.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(output)], **RUN)
    os.unlink(listfile)
    if r.returncode:
        raise RuntimeError(f"ffmpeg lỗi: {_ffmpeg_error(r.stderr)}")
    return output


def _ffmpeg_error(stderr: str) -> str:
    """Rút gọn stderr của ffmpeg: chỉ giữ các dòng báo lỗi (thay vì 800 ký tự cuối lẫn metadata)."""
    keys = ("Error", "error", "Invalid", "No such file", "does not contain", "Unable", "failed")
    lines = [ln.strip() for ln in stderr.splitlines() if any(k in ln for k in keys)]
    return " | ".join(lines[-4:])[:500] or stderr[-300:]


def _duration(ffmpeg: str, path: Path) -> float:
    r = subprocess.run([ffmpeg, "-i", str(path)], **RUN)
    import re
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def mux_voice(video: Path, voice_wav: Path, output: Path, ambient_vol: float = 0.25) -> Path:
    """Thay giọng Veo bằng giọng TTS cố định; giữ ambient Veo nhỏ phía dưới.
    Nếu giọng dài hơn clip thì giữ khung hình cuối cho đủ; ngắn hơn thì để im lặng."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    vdur = _duration(ffmpeg, video)
    vo = _duration(ffmpeg, voice_wav)
    tempo = 1.0
    if vo + 0.3 > vdur:  # giọng dài hơn clip: tăng tốc tối đa 1.3x, phần còn dư mới giữ khung hình cuối
        tempo = min(1.3, max(1.0, vo / max(vdur - 0.4, 1.0)))
    atempo = f"atempo={tempo:.3f}," if tempo > 1.01 else ""
    extra = max(0.0, vo / tempo + 0.3 - vdur)
    vf = f"tpad=stop_mode=clone:stop_duration={extra:.2f}" if extra > 0 else "null"
    has_audio = "Audio:" in subprocess.run([ffmpeg, "-i", str(video)], **RUN).stderr
    if has_audio:
        fc = (f"[0:v]{vf}[v];[0:a]volume={ambient_vol},apad[amb];[1:a]{atempo}apad[vo];"
              f"[amb][vo]amix=inputs=2:duration=longest:normalize=0[a]")
    else:
        fc = f"[0:v]{vf}[v];[1:a]{atempo}apad[a]"
    output.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [ffmpeg, "-y", "-i", str(video), "-i", str(voice_wav), "-filter_complex", fc,
         "-map", "[v]", "-map", "[a]", "-t", f"{vdur + extra:.2f}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2", str(output)],
        **RUN)
    if r.returncode:
        raise RuntimeError(f"ffmpeg lỗi: {_ffmpeg_error(r.stderr)}")
    return output
