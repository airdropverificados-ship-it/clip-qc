"""Tests for clip-qc.

Fixtures are generated with ffmpeg itself in setUpClass, so no binary
test files are checked into the repo. Run with:
    python3 -m unittest discover -s tests -v
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clip_qc.cli import main  # noqa: E402
from clip_qc.core import check_clip  # noqa: E402


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *args],
        check=True, timeout=120,
    )


class ClipQcTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg/ffprobe not found")
        cls.tmp = tempfile.mkdtemp(prefix="clip-qc-tests-")

        # Good clip: 2 s of video + a steady tone.
        cls.good = os.path.join(cls.tmp, "good.mp4")
        _ffmpeg(
            "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", cls.good,
        )

        # Video-only clip: 2 s, no audio stream at all.
        cls.video_only = os.path.join(cls.tmp, "video_only.mp4")
        _ffmpeg(
            "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", cls.video_only,
        )

        # Leading-silence clip: 1.5 s of silence, then 1 s of tone.
        cls.leading_silence = os.path.join(cls.tmp, "leading_silence.mp4")
        _ffmpeg(
            "-f", "lavfi", "-i", "color=c=green:s=320x240:d=2.5",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-af", "adelay=1500:all=1",
            "-t", "2.5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", cls.leading_silence,
        )

        # Corrupt file: random bytes, not a video.
        cls.corrupt = os.path.join(cls.tmp, "corrupt.mp4")
        with open(cls.corrupt, "wb") as fh:
            fh.write(os.urandom(2048))

        # Empty file: 0 bytes.
        cls.empty = os.path.join(cls.tmp, "empty.mp4")
        open(cls.empty, "wb").close()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_good_clip_passes(self) -> None:
        report = check_clip(
            self.good, min_duration=1, max_duration=5,
            expect_audio=True, max_silence=0.5,
        )
        self.assertTrue(report.passed, [c.as_dict() for c in report.checks])
        self.assertIsNotNone(report.loudness_lufs)
        self.assertAlmostEqual(report.duration, 2.0, delta=0.2)

    def test_missing_audio_fails_with_expect_audio(self) -> None:
        report = check_clip(self.video_only, expect_audio=True)
        self.assertFalse(report.passed)
        audio = [c for c in report.checks if c.name == "has audio stream"]
        self.assertEqual(len(audio), 1)
        self.assertEqual(audio[0].status, "FAIL")

    def test_leading_silence_over_threshold_fails(self) -> None:
        report = check_clip(self.leading_silence, max_silence=0.5)
        self.assertFalse(report.passed)
        silence = [c for c in report.checks
                   if c.name == "leading/trailing silence"]
        self.assertEqual(len(silence), 1)
        self.assertEqual(silence[0].status, "FAIL")
        self.assertIn("1.5", silence[0].detail)

    def test_leading_silence_under_threshold_passes(self) -> None:
        report = check_clip(self.leading_silence, max_silence=2.0)
        silence = [c for c in report.checks
                   if c.name == "leading/trailing silence"]
        self.assertEqual(silence[0].status, "PASS")

    def test_corrupt_file_fails(self) -> None:
        report = check_clip(self.corrupt)
        self.assertFalse(report.passed)
        self.assertTrue(any(c.status == "FAIL" for c in report.checks))

    def test_empty_file_fails(self) -> None:
        report = check_clip(self.empty)
        self.assertFalse(report.passed)
        self.assertEqual(report.checks[0].name, "file non-empty")
        self.assertEqual(report.checks[0].status, "FAIL")

    def test_missing_file_fails(self) -> None:
        report = check_clip(os.path.join(self.tmp, "does-not-exist.mp4"))
        self.assertFalse(report.passed)
        self.assertEqual(report.checks[0].name, "file exists")
        self.assertEqual(report.checks[0].status, "FAIL")

    def test_duration_bounds(self) -> None:
        too_short = check_clip(self.good, min_duration=5)
        self.assertFalse(too_short.passed)
        too_long = check_clip(self.good, max_duration=1)
        self.assertFalse(too_long.passed)

    def test_cli_check_exit_codes(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["check", self.good, "--expect-audio"])
        self.assertEqual(code, 0)
        self.assertIn("RESULT: PASS", buf.getvalue())

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["check", self.video_only, "--expect-audio"])
        self.assertEqual(code, 1)
        self.assertIn("RESULT: FAIL", buf.getvalue())

    def test_cli_json_output_is_valid_json(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["check", self.good, "--expect-audio", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["result"], "PASS")
        self.assertIsInstance(data["loudness_lufs"], float)
        self.assertAlmostEqual(data["duration_seconds"], 2.0, delta=0.2)

    def test_check_all_totals(self) -> None:
        subdir = os.path.join(self.tmp, "batch")
        os.mkdir(subdir)
        shutil.copy(self.good, os.path.join(subdir, "good.mp4"))
        shutil.copy(self.video_only, os.path.join(subdir, "video_only.mp4"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["check-all", subdir, "--expect-audio"])
        output = buf.getvalue()
        self.assertEqual(code, 1)  # one file failed
        self.assertIn("PASS", output)
        self.assertIn("FAIL", output)
        self.assertIn("1 passed, 1 failed, 2 total", output)


if __name__ == "__main__":
    unittest.main()
