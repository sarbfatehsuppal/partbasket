import os
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parent
compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
if not compiler and os.name == "nt":
    candidate = Path(r"C:\msys64\ucrt64\bin\g++.exe")
    if candidate.is_file():
        compiler = str(candidate)
if not compiler:
    raise SystemExit("Install a C++17 compiler or set CXX to the g++ or clang++ executable.")
target = root / "build" / ("optimizer.exe" if os.name == "nt" else "optimizer")
target.parent.mkdir(exist_ok=True)
command = [compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic"]
environment = os.environ.copy()
if os.name == "nt":
    command.append("-static")
    compiler_dir = Path(shutil.which(compiler) or compiler).resolve().parent
    environment["PATH"] = f"{compiler_dir}{os.pathsep}{environment.get('PATH', '')}"
subprocess.run([*command, str(root / "optimizer.cpp"), "-o", str(target)],
               env=environment, check=True)
print(f"Built {target.name}")
