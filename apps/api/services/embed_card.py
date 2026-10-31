"""The picture a link preview (Discord, Slack, …) shows for a shared video.

A video is never embedded as a player: an unfurl is a still frame of the video
with a play button drawn over it, and the page link opens the video in the
browser. The frame comes from the video itself: the poster frame the transcode
extracts (at its start, so it exists while the video is still processing), or,
before the transcode has run, a frame FFmpeg reads straight from the uploaded
original.
"""
import io
import logging
import subprocess
from typing import NamedTuple, Optional

from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageDraw

from ..config import settings
from ..models.asset import MediaFile
from .s3_service import generate_internal_presigned_get_url, get_s3_client
from .streaming import original_available

logger = logging.getLogger(__name__)

# Same box the transcoder's poster frames fit in.
_MAX_EDGE = 1280
_FRAME_TIMEOUT_SECONDS = 30
_SUPERSAMPLE = 4


class CardSource(NamedTuple):
    s3_key: str
    # True: `s3_key` is a still (the poster frame); False: a video FFmpeg reads a frame from.
    is_still: bool


def card_source(media_file: MediaFile) -> Optional[CardSource]:
    """Where a video version's card frame comes from: its poster frame, else its
    web MP4, else its uploaded original while that still exists."""
    if media_file.s3_key_thumbnail:
        return CardSource(media_file.s3_key_thumbnail, True)
    if media_file.s3_key_download:
        return CardSource(media_file.s3_key_download, False)
    if media_file.s3_key_raw and original_available(media_file):
        return CardSource(media_file.s3_key_raw, False)
    return None


def video_card(media_file: MediaFile) -> Optional[bytes]:
    """The JPEG a link preview shows for this video version, or None when no
    frame can be had (logged)."""
    source = card_source(media_file)
    if source is None:
        return None
    try:
        frame = read_frame(source.s3_key) if source.is_still else extract_frame(source.s3_key)
        if frame is None:
            logger.warning("No frame for the embed card of media file %s from %s", media_file.id, source.s3_key)
            return None
        return play_card(frame)
    except (BotoCoreError, ClientError, OSError, ValueError) as exc:
        logger.warning("Embed card for media file %s failed: %s", media_file.id, exc)
        return None


def read_frame(s3_key: str) -> bytes:
    """A stored image (the poster frame) as bytes."""
    obj = get_s3_client().get_object(Bucket=settings.s3_bucket, Key=s3_key)
    return obj["Body"].read()


def extract_frame(s3_key: str) -> Optional[bytes]:
    """A representative frame of the video at `s3_key`, read over a presigned
    URL (FFmpeg fetches only the ranges it needs), as JPEG; None when FFmpeg
    cannot produce one in time."""
    url = generate_internal_presigned_get_url(s3_key, expires_in=300)
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", url,
        # `thumbnail` picks the most representative of the first 100 frames,
        # as the transcoder's poster frame does, rather than a black first frame.
        "-vf", f"thumbnail,scale={_MAX_EDGE}:{_MAX_EDGE}:force_original_aspect_ratio=decrease",
        "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "pipe:1",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=_FRAME_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def play_card(frame: bytes) -> bytes:
    """`frame` (any image Pillow reads) with a play button centred on it, as JPEG."""
    image = Image.open(io.BytesIO(frame))
    image.load()
    image = image.convert("RGB")
    image.thumbnail((_MAX_EDGE, _MAX_EDGE))
    width, height = image.size

    # Drawn at several times the size and scaled down: Pillow does not
    # antialias shapes, and an aliased circle looks broken at this size.
    radius = max(12, round(min(width, height) * 0.12))
    side = 2 * radius * _SUPERSAMPLE
    button = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(button)
    ring = max(1, round(side * 0.035))
    draw.ellipse((0, 0, side - 1, side - 1), fill=(0, 0, 0, 150), outline=(255, 255, 255, 235), width=ring)
    # A triangle's centroid sits a third of its width from the flat side, so
    # it is shifted right to look centred.
    tri_h = side * 0.42
    tri_w = tri_h * 0.87
    left = (side - tri_w) / 2 + tri_w * 0.12
    top = (side - tri_h) / 2
    draw.polygon(
        [(left, top), (left, top + tri_h), (left + tri_w, side / 2)],
        fill=(255, 255, 255, 245),
    )
    button = button.resize((2 * radius, 2 * radius), Image.LANCZOS)

    image.paste(button, ((width - 2 * radius) // 2, (height - 2 * radius) // 2), button)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=88)
    return out.getvalue()
