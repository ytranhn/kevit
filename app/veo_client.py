"""Veo 3.1 qua Gemini API: prompt = ảnh nhân vật (reference) + visual + thuyết minh."""
from __future__ import annotations

import time
from pathlib import Path


from .models import Character, Project, Scene
from .settings import VEO_MODEL, get_api_key

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def build_prompt(project: Project, scene: Scene, chars: dict[str, Character]) -> str:
    cast = "\n".join(
        f"- {n}: {chars[n].description}" for n in scene.characters if n in chars and chars[n].description)
    parts = [f"{scene.visual}", f"Style: {project.style}."]
    if project.aspect_ratio == "9:16":
        parts.append("Vertical 9:16 framing, subjects centered, composed for a phone screen.")
    elif project.aspect_ratio == "16:9":
        parts.append("Horizontal 16:9 widescreen framing.")
    if cast:
        parts.append("Characters (keep their appearance identical to the reference images):\n" + cast)
    parts.append("Audio: NO speech, NO dialogue, NO narration, NO music, characters do not speak. "
                 "Only subtle ambient sound effects.")
    return "\n\n".join(parts)


def generate_clip(project: Project, scene: Scene, chars: dict[str, Character],
                  out_dir: Path, log=print) -> Path:
    from google import genai          # nhập muộn: thư viện nặng (~0,3s), chỉ cần khi thật sự gọi Gemini/Veo
    from google.genai import types
    client = genai.Client(api_key=get_api_key())
    refs = []
    for n in scene.characters[:3]:
        img = chars.get(n) and chars[n].image
        if img and Path(img).exists():
            refs.append(types.VideoGenerationReferenceImage(
                image=types.Image(image_bytes=Path(img).read_bytes(),
                                  mime_type=MIME.get(Path(img).suffix.lower(), "image/png")),
                reference_type=types.VideoGenerationReferenceType.ASSET))
    prompt = build_prompt(project, scene, chars)
    ar = project.aspect_ratio if project.aspect_ratio in ("9:16", "16:9") else "16:9"
    base = dict(aspect_ratio=ar, number_of_videos=1)

    # Ưu tiên: ảnh tham chiếu ở đúng tỉ lệ dự án (video dọc). Veo bắt buộc 8s khi dùng ảnh tham chiếu.
    # Nếu API từ chối tổ hợp này -> lùi về image-to-video: ảnh nhân vật đầu tiên làm khung hình mở đầu.
    if refs:
        try:
            op = client.models.generate_videos(
                model=VEO_MODEL, prompt=prompt,
                config=types.GenerateVideosConfig(**base, duration_seconds=8, reference_images=refs))
        except Exception as e:  # noqa: BLE001
            log(f"Scene {scene.index}: Veo không nhận ảnh tham chiếu ở {ar} ({str(e)[:120]}) -> dùng ảnh nhân vật làm khung đầu.")
            first = refs[0].image
            op = client.models.generate_videos(
                model=VEO_MODEL, prompt=prompt, image=first,
                config=types.GenerateVideosConfig(**base, duration_seconds=8))
    else:
        op = client.models.generate_videos(
            model=VEO_MODEL, prompt=prompt,
            config=types.GenerateVideosConfig(**base, duration_seconds=scene.duration))
    while not op.done:
        log(f"Scene {scene.index}: đang render...")
        time.sleep(10)
        op = client.operations.get(op)
    if op.error or not op.response or not op.response.generated_videos:
        raise RuntimeError(f"Veo lỗi: {op.error or 'không có video (có thể bị safety filter)'}")
    video = op.response.generated_videos[0]
    client.files.download(file=video.video)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"scene_{scene.index:02d}_raw.mp4"
    video.video.save(str(dst))
    return dst
