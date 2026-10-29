"""The transcoder reports a poster frame and the display size before it encodes.

A share link pasted while a video is still processing is unfurled (and cached)
from that moment, so the picture and the size have to exist from the start of
the transcode rather than its end. TranscodeJob.early_meta_cb carries them out;
none of it may fail the transcode.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from packages.transcoder.base import TranscodeJob
from packages.transcoder.ffmpeg_transcoder import FFmpegTranscoder, _thumbnail_filter

PREFIX = "processed/p/a/v"


def _transcode(stream_extra=None, *, early_thumb_fails=False, callback=None):
    """A whole transcode with ffmpeg and S3 mocked. Returns the result, every
    command run, each early callback as (args, commands run by then), and the
    uploaded keys."""
    calls: list[list[str]] = []
    reported: list[tuple[tuple, int]] = []

    def run(cmd, **_kwargs):
        calls.append(cmd)
        mock = MagicMock(returncode=0, stderr="", stdout="")
        if "-select_streams" in cmd and cmd[cmd.index("-select_streams") + 1] == "V:0":
            mock.stdout = json.dumps({"streams": [{
                "r_frame_rate": "25/1", "duration": 6.0, "width": 1920, "height": 1080,
                **(stream_extra or {}),
            }]})
        elif "-select_streams" in cmd and cmd[cmd.index("-select_streams") + 1] == "a":
            mock.stdout = json.dumps({"streams": [{"codec_name": "aac"}]})
        elif early_thumb_fails and cmd[-1].endswith("early_thumb.jpg"):
            mock.returncode, mock.stderr = 1, "Invalid data found when processing input"
        return mock

    real_exists = Path.exists

    def exists(self):
        return self.name in ("thumb_0001.jpg", "early_thumb.jpg") or real_exists(self)

    def record(*args):
        reported.append((args, len(calls)))
        if callback:
            callback(*args)

    job = TranscodeJob(media_id="m", version_id="v", input_s3_key="raw/p/a/v/original.mp4",
                       output_s3_prefix=PREFIX, qualities=["720p"], early_meta_cb=record)
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = "https://s3.example.com/raw/p/a/v/original.mp4"
    with patch("subprocess.run", side_effect=run), \
         patch("packages.transcoder.ffmpeg_transcoder.get_backend", return_value="cpu"), \
         patch("builtins.open", MagicMock()), \
         patch("pathlib.Path.glob", return_value=[]), \
         patch("pathlib.Path.rglob", return_value=[]), \
         patch("pathlib.Path.mkdir"), \
         patch.object(Path, "exists", exists), \
         patch("shutil.rmtree"):
        result = asyncio.run(FFmpegTranscoder(s3, "bucket").transcode(job))
    return result, calls, reported, [c.args[2] for c in s3.upload_file.call_args_list]


def test_the_poster_frame_and_size_are_reported_right_after_the_probe():
    result, calls, reported, uploads = _transcode()

    assert result.success, result.error
    assert len(reported) == 1
    args, commands_by_then = reported[0]
    assert args == (1920, 1080, 6.0, f"{PREFIX}/thumbnail.jpg")
    # Only the probe and the frame itself ran before it: nothing was encoded yet.
    assert commands_by_then == 2
    early = calls[1]
    assert early[-1].endswith("early_thumb.jpg")
    assert early[early.index("-vf") + 1] == _thumbnail_filter(is_hdr=False, dv_software=False)
    # The end-of-transcode frame is still made, to the same key.
    assert uploads.count(f"{PREFIX}/thumbnail.jpg") == 2
    assert result.thumbnail_keys == [f"{PREFIX}/thumbnail.jpg"]


def test_a_turned_source_reports_its_display_size():
    _, _, reported, _ = _transcode({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]})

    assert reported[0][0][:2] == (1080, 1920)


def test_a_failing_early_frame_reports_the_size_and_keeps_transcoding():
    result, _, reported, uploads = _transcode(early_thumb_fails=True)

    assert result.success, result.error
    assert reported[0][0] == (1920, 1080, 6.0, None)
    assert uploads.count(f"{PREFIX}/thumbnail.jpg") == 1
    assert result.thumbnail_keys == [f"{PREFIX}/thumbnail.jpg"]


def test_a_raising_callback_does_not_fail_the_transcode():
    def boom(*_args):
        raise RuntimeError("database went away")

    result, _, reported, _ = _transcode(callback=boom)

    assert result.success, result.error
    assert len(reported) == 1


def test_without_a_callback_no_early_frame_is_made():
    calls: list[list[str]] = []
    job = TranscodeJob(media_id="m", version_id="v", input_s3_key="raw/x.mp4",
                       output_s3_prefix=PREFIX, qualities=["720p"])
    assert job.early_meta_cb is None

    def run(cmd, **_kwargs):
        calls.append(cmd)
        if "-select_streams" in cmd:
            return MagicMock(returncode=0, stderr="", stdout=json.dumps({"streams": [
                {"r_frame_rate": "25/1", "duration": 6.0, "width": 1280, "height": 720}]}))
        return MagicMock(returncode=0, stderr="", stdout="")

    s3 = MagicMock()
    s3.generate_presigned_url.return_value = "https://s3.example.com/raw/x.mp4"
    with patch("subprocess.run", side_effect=run), \
         patch("packages.transcoder.ffmpeg_transcoder.get_backend", return_value="cpu"), \
         patch("builtins.open", MagicMock()), \
         patch("pathlib.Path.glob", return_value=[]), \
         patch("pathlib.Path.rglob", return_value=[]), \
         patch("pathlib.Path.mkdir"), \
         patch("shutil.rmtree"):
        asyncio.run(FFmpegTranscoder(s3, "bucket").transcode(job))

    assert not [c for c in calls if c[-1].endswith("early_thumb.jpg")]
