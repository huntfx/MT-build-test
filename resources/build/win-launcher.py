import os
import sys
import glob
import subprocess
import time
from pathlib import Path
from packaging.version import parse as parse_version

# Define the naming pattern for your versioned executables
# Matches: MouseTracks-2.2.7-windows-x64.exe
EXE_PREFIX = "MouseTracks-"
EXE_SUFFIX = "-windows-x64.exe"

def get_executable_versions(directory: Path) -> list[tuple[any, Path]]:
    """Finds all versioned executables and returns them sorted by version."""
    executables = []
    pattern = str(directory / f"{EXE_PREFIX}*{EXE_SUFFIX}")

    for file_path in glob.glob(pattern):
        path = Path(file_path)
        # Extract version string: "MouseTracks-2.2.7-..." -> "2.2.7"
        try:
            # Remove prefix
            name = path.name[len(EXE_PREFIX):]
            # Remove suffix
            version_str = name[:-len(EXE_SUFFIX)]

            version = parse_version(version_str)
            executables.append((version, path))
        except Exception:
            continue # Skip files that don't match the version parsing logic

    # Sort by version (newest first)
    executables.sort(key=lambda x: x[0], reverse=True)
    return executables

def cleanup_old_versions(executables: list[tuple[any, Path]], keep: int = 2):
    """Deletes older versions, keeping the specified amount."""
    if len(executables) <= keep:
        return

    for _, path in executables[keep:]:
        try:
            print(f"Cleaning up old version: {path.name}")
            # Try to remove. If it's locked (running), this will fail safely.
            os.remove(path)
        except OSError:
            pass

def main():
    current_dir = Path(sys.executable).parent

    # 1. Find versions
    versions = get_executable_versions(current_dir)

    if not versions:
        # Fallback: If no versioned files found, maybe we are the only one?
        # Or show an error.
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, "No installed version of MouseTracks found.", "Launch Error", 0x10)
        sys.exit(1)

    # 2. Identify latest
    latest_version, latest_path = versions[0]

    # 3. Cleanup old versions (Keep current + 1 backup)
    cleanup_old_versions(versions, keep=2)

    # 4. Launch the latest version
    # We pass all arguments received by the launcher to the child process
    # We also add a flag so the child knows it was launched by the wrapper
    cmd = [str(latest_path), '--launched-by-wrapper'] + sys.argv[1:]

    # Use subprocess.Popen to launch and detach
    subprocess.Popen(cmd, cwd=current_dir)

    # 5. Exit immediately so the launcher doesn't stay open
    sys.exit(0)

if __name__ == "__main__":
    main()
