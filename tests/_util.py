"""Shared test plumbing: fixture generation (tiny synthetic media via
ffmpeg, cached in tests/fixtures/generated/) and CLI runners. No network,
no spend."""
import json, os, subprocess, sys

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS)
TOOLS = os.path.join(REPO, "tools")
FIX = os.path.join(TESTS, "fixtures")
GEN = os.path.join(FIX, "generated")

sys.path.insert(0, TOOLS)

# 24fps matches real lineup content; extract_stills' last-frame inset (50ms)
# needs a frame interval below it
SIZE, RATE = "320x180", 24

def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)

def clip(name, dur, vf=None):
    """Deterministic testsrc2 clip; optional extra filter (e.g. drawbox)."""
    path = os.path.join(GEN, name)
    if not os.path.exists(path):
        filt = f"testsrc2=duration={dur}:size={SIZE}:rate={RATE}"
        if vf:
            filt += "," + vf
        _ff("-f", "lavfi", "-i", filt, "-pix_fmt", "yuv420p",
            "-preset", "ultrafast", path)
    return path

def still(name="still.png"):
    path = os.path.join(GEN, name)
    if not os.path.exists(path):
        _ff("-f", "lavfi", "-i", f"testsrc2=duration=0.1:size={SIZE}:rate=1",
            "-frames:v", "1", path)
    return path

def ensure_fixtures():
    os.makedirs(GEN, exist_ok=True)
    # lineup: SH001 5s | SH002 2.5s (sub-4s) | SH003 4s | scene 2: SH004 4s
    clip("SQ001-SC001-SH001.mp4", 5)
    clip("SQ001-SC001-SH002.mp4", 2.5)
    clip("SQ001-SC001-SH003.mp4", 4)
    clip("SQ001-SC002-SH004.mp4", 4)
    # diff_gate: change inside an authorized region vs smuggled outside it
    box = "drawbox=x={x}:y={y}:w=64:h=36:color=red:t=fill"
    clip("SH001_authorized.mp4", 5, vf=box.format(x=224, y=126))
    clip("SH001_unauthorized.mp4", 5, vf=box.format(x=32, y=18))
    clip("SH001_short.mp4", 3)      # runtime mismatch candidate
    still()
    manifest = [{"shot_id": "SQ001-SC001-SH001", "duration_s": 5.0},
                {"shot_id": "SQ001-SC001-SH002", "duration_s": 2.5},
                {"shot_id": "SQ001-SC001-SH003", "duration_s": 4.0},
                {"shot_id": "SQ001-SC002-SH004", "duration_s": 4.0}]
    with open(os.path.join(GEN, "shots.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    return GEN

def run_tool(tool, *args, cwd=None):
    """Run a tools/ CLI; returns CompletedProcess with text output."""
    return subprocess.run([sys.executable, os.path.join(TOOLS, tool), *args],
                          capture_output=True, text=True, cwd=cwd or REPO)

def stderr_error(cp):
    """Parse the JSON error object a failing tool must emit on stderr."""
    line = cp.stderr.strip().splitlines()[-1]
    return json.loads(line)
