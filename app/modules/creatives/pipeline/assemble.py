"""ffmpeg assembly: burn in each scene's on-screen text, normalize every
clip to a consistent stream layout, append a logo/CTA/disclaimer end card,
and concatenate into the final reel. Ported from the prototype's
app/pipeline/assemble.py, adapted to take/return in-memory bytes -- scene
clips are written into a throwaway tempfile.TemporaryDirectory() (ffmpeg
genuinely needs real files) and the final mp4 is read back into memory
before that directory is discarded, rather than landing in a persistent
downloads/ folder.

Uses ffmpeg directly via subprocess (with ffmpeg-python only for probing)
rather than fighting ffmpeg-python's filter-graph API for a pipeline this
specific -- more transparent and debuggable.

Clips from different providers/scenes can differ in resolution, fps, and
whether they even have an audio stream at all (the mock provider's lavfi
color clips have none; a real Veo clip with generate_audio=True does).
normalize_clip() forces every clip to the same resolution/fps/codec and
guarantees an audio stream (adding silence if the source has none), so the
final concat step can use stream copy instead of a fragile filter graph.
"""

from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

import ffmpeg
from PIL import Image, ImageDraw, ImageFont

from app.models.enums import CreativeAssetKind
from app.modules.creatives.brand import BrandProfileDTO
from app.modules.creatives.domain import GeneratedConcept
from app.modules.creatives.pipeline.postprocess import _load_font
from app.modules.creatives.pipeline.types import VIDEO_MIME_TYPE, RenderedAsset

REEL_SIZE = (1080, 1920)
FPS = 30
END_CARD_DURATION_S = 3.0


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {result.stderr[-2000:]}")


def _has_audio_stream(path: Path) -> bool:
    try:
        info = ffmpeg.probe(str(path))
    except ffmpeg.Error:
        return False
    return any(stream.get("codec_type") == "audio" for stream in info.get("streams", []))


def _escape_drawtext(text: str) -> str:
    """ffmpeg drawtext treats `:`, `'`, and `\\` as syntax -- escape them."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")


def normalize_clip(src: Path, dest: Path, *, on_screen_text: str = "") -> None:
    w, h = REEL_SIZE
    vf_parts = [
        f"scale={w}:{h}:force_original_aspect_ratio=increase",
        f"crop={w}:{h}",
        f"fps={FPS}",
    ]
    if on_screen_text:
        safe_text = _escape_drawtext(on_screen_text)
        vf_parts.append(
            f"drawtext=text='{safe_text}':fontcolor=white:fontsize=54:"
            "box=1:boxcolor=0x0A2540@0.75:boxborderw=20:x=(w-text_w)/2:y=h-th-140"
        )
    vf = ",".join(vf_parts)

    args = ["-i", str(src)]
    if _has_audio_stream(src):
        args += ["-vf", vf, "-af", "aresample=44100"]
    else:
        args += [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-vf",
            vf,
            "-map",
            "0:v",
            "-map",
            "1:a",
        ]
    args += ["-c:v", "libx264", "-c:a", "aac", "-shortest", str(dest)]
    _run_ffmpeg(args)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def build_end_card(
    brand: BrandProfileDTO,
    *,
    cta: str,
    disclaimer: str,
    logo_bytes: bytes | None,
    out_path: Path,
) -> None:
    w, h = REEL_SIZE
    bg_color = brand.visual_identity.palette[0] if brand.visual_identity.palette else "#0A2540"
    img = Image.new("RGB", (w, h), color=bg_color)
    draw = ImageDraw.Draw(img)

    if logo_bytes:
        logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
        max_w = int(w * 0.5)
        if logo.width > max_w > 0:
            ratio = max_w / logo.width
            logo = logo.resize((max_w, max(1, int(logo.height * ratio))))
        img.paste(logo, ((w - logo.width) // 2, int(h * 0.32)), logo)

    cta_font = _load_font(int(h * 0.032))
    max_text_width = int(w * 0.8)
    cta_lines = _wrap_text(draw, cta, cta_font, max_text_width)
    line_h = int(h * 0.045)
    cta_y = int(h * 0.55)
    for line in cta_lines:
        bbox = draw.textbbox((0, 0), line, font=cta_font)
        draw.text(((w - (bbox[2] - bbox[0])) // 2, cta_y), line, font=cta_font, fill="white")
        cta_y += line_h

    disc_font = _load_font(int(h * 0.018))
    disc_lines = _wrap_text(draw, disclaimer, disc_font, max_text_width)
    disc_y = int(h * 0.88)
    for line in disc_lines:
        bbox = draw.textbbox((0, 0), line, font=disc_font)
        draw.text(((w - (bbox[2] - bbox[0])) // 2, disc_y), line, font=disc_font, fill="#CCCCCC")
        disc_y += int(h * 0.026)

    png_path = out_path.with_suffix(".png")
    img.save(png_path)
    try:
        _run_ffmpeg(
            [
                "-loop",
                "1",
                "-i",
                str(png_path),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=stereo",
                "-t",
                str(END_CARD_DURATION_S),
                "-vf",
                f"fps={FPS},format=yuv420p",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                str(out_path),
            ]
        )
    finally:
        png_path.unlink(missing_ok=True)


def stitch_reel(clip_paths: list[Path], out_path: Path) -> None:
    """Concatenates already-normalized clips (same codec/resolution/fps/
    audio layout) via ffmpeg's concat demuxer with stream copy -- fast, no
    re-encoding, and reliable because everything upstream was normalized."""
    with tempfile.TemporaryDirectory() as tmp:
        list_file = Path(tmp) / "concat_list.txt"
        list_file.write_text(
            "\n".join(f"file '{p.resolve().as_posix()}'" for p in clip_paths), encoding="utf-8"
        )
        _run_ffmpeg(
            ["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)]
        )


def assemble_reel(
    concept: GeneratedConcept,
    concept_index: int,
    *,
    scene_clip_bytes: list[bytes],
    brand: BrandProfileDTO,
    logo_bytes: bytes | None = None,
) -> RenderedAsset:
    """scene_clip_bytes must be in scene order, one raw clip per
    concept.reel.scenes. Everything (raw clips, normalized intermediates,
    end card) lives in one throwaway temp directory; only the final mp4's
    bytes survive past this call."""
    if concept.reel is None:
        raise ValueError(f"concept {concept_index} has no reel script")
    if len(scene_clip_bytes) != len(concept.reel.scenes):
        raise ValueError(
            f"expected {len(concept.reel.scenes)} scene clips, got {len(scene_clip_bytes)}"
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        normalized_paths: list[Path] = []
        scenes_and_clips = zip(concept.reel.scenes, scene_clip_bytes, strict=True)
        for i, (scene, clip_bytes) in enumerate(scenes_and_clips):
            src = tmp_dir / f"scene_{i + 1:02d}_raw.mp4"
            src.write_bytes(clip_bytes)
            dest = tmp_dir / f"scene_{i + 1:02d}_final.mp4"
            normalize_clip(src, dest, on_screen_text=scene.on_screen_text)
            normalized_paths.append(dest)

        end_card_path = tmp_dir / "end_card.mp4"
        build_end_card(
            brand,
            cta=concept.cta,
            disclaimer=concept.disclaimer_line,
            logo_bytes=logo_bytes,
            out_path=end_card_path,
        )

        final_path = tmp_dir / "final_reel.mp4"
        stitch_reel([*normalized_paths, end_card_path], final_path)
        final_bytes = final_path.read_bytes()

    return RenderedAsset(
        kind=CreativeAssetKind.video,
        concept_index=concept_index,
        label="reel",
        model_id="",
        mime_type=VIDEO_MIME_TYPE,
        data=final_bytes,
    )
