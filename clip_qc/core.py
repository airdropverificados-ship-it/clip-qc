"""Core checks for clip-qc.

Every check runs ffprobe/ffmpeg as a subprocess and parses its output.
Only the Python standard library is used.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field

FFPROBE = "ffprobe"
FFMPEG = "ffmpeg"

SILENCE_NOISE = "-50dB"   # anything quieter than this counts as silence
SILENCE_MIN = 0.1         # minimum silence length (s) silencedetect reports
LOUDNESS_WARN_LOW = -24.0  # LUFS; outside -24..-12 LUFS is a warning, not a fail
LOUDNESS_WARN_HIGH = -12.0


@dataclass
class Check:
    """One check result."""

    name: str
    status: str  # "PASS", "FAIL" or "SKIP"
    detail: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class ClipReport:
    """Full report for one clip."""

    path: str
    checks: list[Check] = field(default_factory=list)
    duration: float | None = None
    loudness_lufs: float | None = None

    @property
    def passed(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)

    def as_dict(self) -> dict:
        return {
            "file": self.path,
            "result": "PASS" if self.passed else "FAIL",
            "duration_seconds": self.duration,
            "loudness_lufs": self.loudness_lufs,
            "checks": [c.as_dict() for c in self.checks],
        }


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=300,
    )


def _probe(path: str) -> dict | None:
    """Return ffprobe's JSON output for the file, or None if unreadable."""
    try:
        proc = _run([
            FFPROBE, "-v", "error", "-show_format", "-show_streams",
            "-of", "json", path,
        ])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


_SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END = re.compile(
    r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)"
)


def _silences(path: str) -> list[tuple[float, float]] | None:
    """Return [(start, end), ...] silence intervals, or None if no audio."""
    try:
        proc = _run([
            FFMPEG, "-v", "info", "-i", path,
            "-af", f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_MIN}",
            "-f", "null", "-",
        ])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    intervals: list[tuple[float, float]] = []
    pending_start: float | None = None
    for line in proc.stderr.splitlines():
        m = _SILENCE_START.search(line)
        if m:
            pending_start = float(m.group(1))
            continue
        m = _SILENCE_END.search(line)
        if m and pending_start is not None:
            intervals.append((pending_start, float(m.group(1))))
            pending_start = None
    return intervals


_LOUDNORM_JSON = re.compile(r"\{[^}]*\"input_i\"[^}]*\}")


def _loudness(path: str) -> float | None:
    """Integrated loudness in LUFS via loudnorm, or None if no audio data."""
    try:
        proc = _run([
            FFMPEG, "-v", "info", "-i", path,
            "-af", "loudnorm=print_format=json", "-f", "null", "-",
        ])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    m = _LOUDNORM_JSON.search(proc.stderr)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    value = data.get("input_i")
    if value in (None, "-inf"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def check_clip(
    path: str,
    min_duration: float | None = None,
    max_duration: float | None = None,
    expect_audio: bool = False,
    max_silence: float | None = None,
) -> ClipReport:
    """Run every applicable check on one clip and return the report."""
    report = ClipReport(path=path)

    # 1. File exists and is non-empty.
    if not os.path.isfile(path):
        report.checks.append(Check("file exists", "FAIL", "file not found"))
        return report
    size = os.path.getsize(path)
    if size == 0:
        report.checks.append(
            Check("file non-empty", "FAIL", "file is 0 bytes"))
        return report
    report.checks.append(
        Check("file exists", "PASS", f"{size} bytes"))

    # 2. ffprobe can read it.
    probe = _probe(path)
    if probe is None:
        report.checks.append(
            Check("readable by ffprobe", "FAIL", "ffprobe could not parse it"))
        return report
    report.checks.append(Check("readable by ffprobe", "PASS"))

    streams = probe.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    # 3. Has a video stream.
    if has_video:
        report.checks.append(Check("has video stream", "PASS"))
    else:
        report.checks.append(Check("has video stream", "FAIL", "no video stream"))

    # 4. Duration within [min, max] when given.
    fmt = probe.get("format", {})
    try:
        report.duration = float(fmt.get("duration", "nan"))
    except (TypeError, ValueError):
        report.duration = None
    if report.duration is None or report.duration != report.duration:  # NaN
        report.checks.append(
            Check("duration", "FAIL", "duration unknown"))
    else:
        detail = f"{report.duration:.2f} s"
        if min_duration is not None and report.duration < min_duration:
            report.checks.append(Check(
                "duration", "FAIL",
                f"{detail} is shorter than minimum {min_duration:g} s"))
        elif max_duration is not None and report.duration > max_duration:
            report.checks.append(Check(
                "duration", "FAIL",
                f"{detail} is longer than maximum {max_duration:g} s"))
        else:
            report.checks.append(Check("duration", "PASS", detail))

    # 5. Audio stream when expected.
    if expect_audio:
        if has_audio:
            report.checks.append(Check("has audio stream", "PASS"))
        else:
            report.checks.append(
                Check("has audio stream", "FAIL", "no audio stream"))

    # 6 & 7. Silence and loudness need an audio stream.
    if not has_audio:
        if max_silence is not None:
            report.checks.append(Check(
                "leading/trailing silence", "SKIP", "no audio stream"))
        report.checks.append(Check(
            "audio loudness", "FAIL", "no audio data"))
        return report

    if max_silence is not None:
        intervals = _silences(path)
        if intervals is None:
            report.checks.append(Check(
                "leading/trailing silence", "FAIL",
                "could not analyse audio"))
        else:
            duration = report.duration or 0.0
            leading = 0.0
            trailing = 0.0
            for start, end in intervals:
                if start <= 0.05:
                    leading = max(leading, end - start)
                if duration and end >= duration - 0.05:
                    trailing = max(trailing, end - start)
            worst = max(leading, trailing)
            if worst > max_silence:
                where = "leading" if leading >= trailing else "trailing"
                report.checks.append(Check(
                    "leading/trailing silence", "FAIL",
                    f"{where} silence {worst:.2f} s exceeds "
                    f"{max_silence:g} s"))
            else:
                report.checks.append(Check(
                    "leading/trailing silence", "PASS",
                    f"leading {leading:.2f} s, trailing {trailing:.2f} s"))

    # 7. Loudness: report LUFS; FAIL only if no audio data or the whole
    # clip is silent. Outside -24..-12 LUFS is a warning, not a failure.
    loudness = _loudness(path)
    report.loudness_lufs = loudness
    if loudness is None:
        report.checks.append(Check(
            "audio loudness", "FAIL",
            "no measurable audio (silent or empty audio track)"))
    else:
        detail = f"integrated loudness {loudness:.1f} LUFS"
        if LOUDNESS_WARN_LOW <= loudness <= LOUDNESS_WARN_HIGH:
            report.checks.append(Check("audio loudness", "PASS", detail))
        else:
            report.checks.append(Check(
                "audio loudness", "PASS",
                f"{detail} (warning: outside the usual "
                f"{LOUDNESS_WARN_LOW:g}..{LOUDNESS_WARN_HIGH:g} LUFS range)"))

    return report
