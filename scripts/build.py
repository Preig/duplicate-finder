"""Build and test standalone releases on their native OS. Build-time Python only."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
MAC = sys.platform == "darwin"
if sys.platform not in ("win32", "darwin"):
    raise SystemExit("Build on Windows or macOS.")
arch = platform.machine().lower()
label = "macOS-Apple-Silicon" if MAC and arch == "arm64" else "macOS-Intel" if MAC else "Windows-x64"
release = ROOT / "release"
release.mkdir(exist_ok=True)
args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "Duplicate Finder", "--onefile" if not MAC else "--onedir"]
if MAC:
    args += ["--osx-bundle-identifier", "io.github.preig.duplicatefinder", "--target-arch", arch,
             "--codesign-identity", "-"]
args += ["duplicate_finder.pyw"]
subprocess.run(args, check=True)
executable = ROOT / "dist" / ("Duplicate Finder.app/Contents/MacOS/Duplicate Finder" if MAC else "Duplicate Finder.exe")
report = release / f"Verification-{label}.json"
environment = os.environ.copy()
# Ensure the bundle does not depend on a Python environment or Python on PATH.
for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
    environment.pop(key, None)
environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin" if MAC else str(Path(os.environ["SystemRoot"]) / "System32")
subprocess.run([str(executable), "--self-test", str(report)], check=True, env=environment, timeout=180)
data = json.loads(report.read_text(encoding="utf-8"))
assert data["bundled_runtime"] and data["status"] == "passed"
archive = release / f"Duplicate-Finder-{label}.zip"
instructions = ("DUPLICATE FINDER\n\nNo Python or additional runtime is needed.\n\n"
    + ("Extract the ZIP. Open Duplicate Finder.app. You can drag the app to Applications.\n"
       if MAC else "Extract the ZIP. Double-click Duplicate Finder.exe.\n")
    + "These builds are unsigned (the Mac app is ad-hoc signed, not notarized). Your OS may request approval on first open.\n"
      "Find updates and instructions at https://github.com/Preig/duplicate-finder\n\n"
      "Nothing is deleted until you select files and confirm. Files go to Trash / Recycle Bin.\n")
if MAC:
    # ditto preserves the symlinks and metadata within the .app bundle.
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                    str(ROOT / "dist" / "Duplicate Finder.app"), str(archive)], check=True)
with zipfile.ZipFile(archive, "a" if MAC else "w", zipfile.ZIP_DEFLATED) as bundle:
    if not MAC:
        bundle.write(executable, "Duplicate Finder.exe")
    bundle.writestr("START-HERE.txt", instructions)
    bundle.writestr("VERIFICATION.json", report.read_bytes())
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(release / f"SHA256-{label}.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
print(json.dumps({"asset": archive.name, "sha256": digest, "self_test": data}, indent=2))
