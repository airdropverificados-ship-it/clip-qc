# clip-qc

**Check video clips before you publish them.**

`clip-qc` is a small command-line tool that runs a batch of sanity checks on
a video file — is it readable, does it have video and audio, is the duration
right, does it start or end with dead silence, how loud is it — so you catch
bad clips *before* you upload them instead of after your audience does.

It shells out to `ffprobe`/`ffmpeg` and parses their output. No video is
ever re-encoded, and no clip leaves your machine.

## What it checks

| Check | What it does |
|---|---|
| File exists | The file is there and is not 0 bytes |
| Readable by ffprobe | `ffprobe` can parse the container |
| Has video stream | The file contains at least one video stream |
| Duration | Within `--min-duration` / `--max-duration` when given |
| Has audio stream | Only when `--expect-audio` is passed |
| Leading/trailing silence | Fails if silence at the start or end is longer than `--max-silence` seconds (via `ffmpeg` `silencedetect`, −50 dB threshold) |
| Audio loudness | Reports integrated loudness in LUFS (via `ffmpeg` `loudnorm`). It **fails** only if there is no audio data at all or the whole clip is silent. A value outside the usual −24..−12 LUFS range is reported as a warning, not a failure — the tool does not invent a loudness threshold for you. |

## Install

Requirements:

- Python 3.10 or newer
- `ffmpeg` and `ffprobe` on your `PATH` (the tool uses the system binaries)

No pip packages are needed — the tool uses only the Python standard library,
so there is no `requirements.txt`.

```sh
git clone https://github.com/airdropverificados-ship-it/clip-qc.git
cd clip-qc
pip install .
```

Or run it without installing, straight from the checkout:

```sh
python3 -m clip_qc check clip.mp4
```

## Usage

### Check a single clip

```sh
clip-qc check episode-03.mp4 --min-duration 30 --max-duration 240 \
    --expect-audio --max-silence 1
```

### Check every clip in a directory

```sh
clip-qc check-all ./exports/ --expect-audio --max-silence 1
```

`check-all` looks at every `.mp4`, `.mov` and `.mkv` file in the directory,
prints one summary line per file and a totals line at the end.

### Machine-readable output

```sh
clip-qc check episode-03.mp4 --expect-audio --json
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | All checks passed |
| `1` | At least one check failed (or, for `check-all`, at least one file failed) |
| `2` | Usage error (bad arguments, not a directory) |

## Example output

Real output from a verification run:

```
$ clip-qc check good.mp4 --min-duration 1 --max-duration 5 --expect-audio --max-silence 0.5
/tmp/clipqc-manual/clips/good.mp4:
  PASS  file exists — 22319 bytes
  PASS  readable by ffprobe
  PASS  has video stream
  PASS  duration — 2.00 s
  PASS  has audio stream
  PASS  leading/trailing silence — leading 0.00 s, trailing 0.00 s
  PASS  audio loudness — integrated loudness -21.8 LUFS
  RESULT: PASS

$ clip-qc check leading_silence.mp4 --max-silence 0.5
/tmp/clipqc-manual/clips/leading_silence.mp4:
  PASS  file exists — 15852 bytes
  PASS  readable by ffprobe
  PASS  has video stream
  PASS  duration — 2.52 s
  FAIL  leading/trailing silence — leading silence 1.50 s exceeds 0.5 s
  PASS  audio loudness — integrated loudness -22.5 LUFS
  RESULT: FAIL

$ clip-qc check-all ./clips/ --expect-audio --max-silence 0.5
FAIL  /tmp/clipqc-manual/clips/corrupt.mp4 (1 passed, 1 failed)
PASS  /tmp/clipqc-manual/clips/good.mp4 (7 passed, 0 failed)
FAIL  /tmp/clipqc-manual/clips/leading_silence.mp4 (6 passed, 1 failed)
FAIL  /tmp/clipqc-manual/clips/video_only.mp4 (4 passed, 2 failed)
1 passed, 3 failed, 4 total
```

JSON output for a single clip (`--json`):

```json
{
  "file": "/tmp/clipqc-manual/clips/good.mp4",
  "result": "PASS",
  "duration_seconds": 2.0,
  "loudness_lufs": -21.81,
  "checks": [
    { "name": "file exists", "status": "PASS", "detail": "22319 bytes" },
    { "name": "readable by ffprobe", "status": "PASS", "detail": "" },
    { "name": "has video stream", "status": "PASS", "detail": "" },
    { "name": "duration", "status": "PASS", "detail": "2.00 s" },
    { "name": "has audio stream", "status": "PASS", "detail": "" },
    { "name": "audio loudness", "status": "PASS",
      "detail": "integrated loudness -21.8 LUFS" }
  ]
}
```

## Project structure

```
clip-qc/
├── clip-qc            # launcher script (installed as the `clip-qc` command)
├── clip_qc/
│   ├── __init__.py    # package version
│   ├── __main__.py    # `python3 -m clip_qc` entry point
│   ├── cli.py         # argument parsing, text/JSON output, exit codes
│   └── core.py        # the checks (ffprobe/ffmpeg wrappers)
├── tests/
│   └── test_clip_qc.py
├── LICENSE
├── README.md
└── pyproject.toml
```

## Tests

Tests use only the standard library `unittest` module and generate their own
fixtures with `ffmpeg` (a good clip, a video-only clip, a clip with 1.5 s of
leading silence, a corrupt file and an empty file), so nothing binary is
checked into the repo.

```sh
python3 -m unittest discover -s tests -v
```

## Limitations

- Silence detection uses a fixed −50 dB threshold; very quiet speech or
  music under that level counts as silence.
- Loudness is measured with a single `loudnorm` pass, which is an estimate
  good enough for a pre-publish check, not a broadcast-compliance measurement.
- `check-all` is not recursive: it only looks at files directly inside the
  given directory.

## License

MIT — see [LICENSE](LICENSE).
