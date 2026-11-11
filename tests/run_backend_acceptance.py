"""Reproducible local acceptance tooling; no product/server/frontend features.

prepare copies an explicit source/document set into NEW ignored sandboxes (never
copies .git, data, or environments). install executes the pinned README setup in
each sandbox. verify runs allowlisted offline README/guide commands with network
guards, the full suite, pin/import checks, and export invariants. Live checks are
recorded separately as SKIPPED, not mislabeled as passed/region-blocked.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
GUARD = '''import socket
import requests.sessions
import curl_cffi.requests

def denied(*args, **kwargs):
    raise RuntimeError("Acceptance runtime network is disabled")

socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
requests.sessions.Session.request = denied
curl_cffi.requests.Session.request = denied
curl_cffi.requests.AsyncSession.request = denied
NETWORK_DISABLED = True
'''


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def clean_environment():
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(name, None)
    env.update(PYTHONNOUSERSITE="1", PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return env


def prepare(directory):
    destination = Path(directory).resolve()
    if not destination.is_relative_to((ROOT / "data").resolve()):
        raise ValueError("Acceptance snapshots must be under this project's ignored data directory")
    destination.mkdir(parents=True, exist_ok=False)
    guard = destination / "network-guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(GUARD, encoding="utf-8")
    selected = [Path(name) for name in ("README.md", "main.py", "pyproject.toml", "requirements-build.txt", "requirements.lock.txt", "ChronoChart_Brief.md")]
    selected += [path.relative_to(ROOT) for folder in ("src", "tests", "docs") for path in (ROOT / folder).rglob("*")
                 if path.is_file() and path.suffix in (".py", ".md") and "__pycache__" not in path.parts]
    manifest = {str(path).replace("\\", "/"): hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in selected}
    for mode in ("editable", "wheel"):
        for relative in selected:
            target = destination / mode / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    save(destination / "snapshot.json", dict(created_utc=timestamp(), source_directory=str(ROOT),
                                             starting_revision=revision, sha256=manifest))
    print(f"Prepared independent editable/wheel projects at {destination}")


def call(argv, project, logs, label, *, env=None, expected=0, timeout=300):
    logs.mkdir(parents=True, exist_ok=True)
    started = timestamp()
    result = subprocess.run([str(item) for item in argv], cwd=project, env=env or clean_environment(),
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    log = logs / f"{label}.txt"
    log.write_text(result.stdout + "\n--- stderr ---\n" + result.stderr, encoding="utf-8")
    record = dict(argv=[str(item) for item in argv], cwd=str(project), started_utc=started,
                  finished_utc=timestamp(), returncode=result.returncode, expected_returncode=expected,
                  status="passed" if result.returncode == expected else "failed", log=str(log))
    if result.returncode != expected:
        raise RuntimeError(f"Acceptance command failed: {label}; see {log}")
    return record, result.stdout, result.stderr


def install(project, mode):
    project = Path(project).resolve()
    if project.name != mode or not (project.parent / "snapshot.json").is_file():
        raise ValueError("Install requires a prepared acceptance project")
    venv = project / (".venv" if mode == "editable" else ".venv-cli-check")
    if venv.exists():
        raise FileExistsError("Refusing to reuse/overwrite an acceptance environment")
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    commands = [[sys.executable, "-m", "venv", str(venv)],
                [python, "-m", "pip", "install", "-r", "requirements-build.txt"],
                [python, "-m", "pip", "install", "-r", "requirements.lock.txt"],
                [python, "-m", "pip", "install", *(["-e"] if mode == "editable" else []), ".", "--no-deps", "--no-build-isolation"],
                [python, "-m", "pip", "check"]]
    report = dict(mode=mode, started_utc=timestamp(), status="failed", commands=[],
                  mapping="README cd maps to a new project copy; Python/base location is explicit")
    try:
        for number, command in enumerate(commands):
            record, _, _ = call(command, project, project.parent / f"logs-install-{mode}", f"{number:02d}")
            report["commands"].append(record)
        report["status"] = "passed"
    finally:
        report["finished_utc"] = timestamp()
        save(project.parent / f"install-{mode}.json", report)
    print(f"Pinned README installation passed: {mode}")


def shell_lines(path):
    inside, language = False, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            if inside:
                inside, language = False, None
            else:
                inside, language = True, line.strip()[3:]
            continue
        if inside and language == "powershell" and line.strip() and not line.lstrip().startswith("#"):
            yield line.strip()


def command(line, project):
    """Allowlist README Python/product commands; never execute arbitrary shell."""
    args = [item[1:-1] if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'" else item
            for item in shlex.split(line, posix=False)]
    if not args:
        raise ValueError("Empty README command")
    program = args.pop(0).replace("\\", "/").lower()
    if program == "cd":
        if len(args) != 1:
            raise ValueError("Unsupported README directory command")
        return "setup-separate", args
    if program == "python" or program.endswith("/python.exe"):
        setup = {
            ("-m", "venv", ".venv"), ("-m", "venv", ".venv-cli-check"),
            ("-m", "pip", "install", "-r", "requirements-build.txt"),
            ("-m", "pip", "install", "-r", "requirements.lock.txt"),
            ("-m", "pip", "install", "-e", ".", "--no-deps", "--no-build-isolation"),
            ("-m", "pip", "install", ".", "--no-deps", "--no-build-isolation"),
            ("-m", "pip", "check"),
        }
        if args[:2] in (["-m", "venv"], ["-m", "pip"]):
            if tuple(args) not in setup:
                raise ValueError("README setup differs from the pinned installation profile")
            return "setup-separate", args
    if "--live-binance-check" in args or ("--provider" in args and args[args.index("--provider") + 1] in ("yfinance", "binance")):
        return "live-skipped", args
    if program.endswith("chronochart.exe") or program.endswith("/chronochart"):
        if args != ["--help"]:
            raise ValueError("Only console --help is allowlisted")
        return "console", args
    if program != "python" and not program.endswith("/python.exe"):
        raise ValueError("README command is not an allowlisted Python invocation")
    while args[:2] == ["-W", "error"]:
        args = args[2:]
    if not (args[:1] == ["main.py"] or args[:2] == ["-m", "chronochart"] or args[:2] == ["-m", "unittest"]):
        raise ValueError("Unsupported README Python command")
    for option in ("--output", "--cache-dir"):
        if option in args:
            target = (project / args[args.index(option) + 1]).resolve()
            if not target.is_relative_to(project.resolve()):
                raise ValueError("README output/cache path escapes the clean sandbox")
    return "offline", args


def verify(project, mode):
    project = Path(project).resolve()
    report = dict(mode=mode, started_utc=timestamp(), status="failed", commands=[], live_provider_checks=[
        dict(provider="yfinance", status="skipped", reason="Deterministic acceptance is offline; service access/terms and real quotes are unverified. Adapter tests use mocks."),
        dict(provider="binance", status="skipped", reason="Deterministic acceptance is offline; regional availability/terms are not inferred or bypassed. Adapter tests use mocks."),
    ], deferred=["indicators", "live updates", "HTTP API development", "frontend work"])
    logs = project.parent / f"logs-verify-{mode}"
    guard = project.parent / "network-guard"
    guard.mkdir(exist_ok=True)
    guard_file = guard / "sitecustomize.py"
    if guard_file.exists() and guard_file.read_text(encoding="utf-8") != GUARD:
        raise ValueError("Refusing to overwrite an unfamiliar network guard")
    if not guard_file.exists():
        with guard_file.open("x", encoding="utf-8") as stream:
            stream.write(GUARD)
    env = clean_environment()
    env["PYTHONPATH"] = str(guard)
    try:
        import chronochart
        package = Path(chronochart.__file__).resolve()
        if sys.prefix == sys.base_prefix:
            raise RuntimeError("Acceptance must use its fresh virtual environment")
        expected_root = project / "src" if mode == "editable" else Path(sys.prefix)
        if not package.is_relative_to(expected_root):
            raise RuntimeError(f"Wrong import source: {package}")
        snapshot = json.loads((project.parent / "snapshot.json").read_text(encoding="utf-8"))
        for name, expected_hash in snapshot["sha256"].items():
            if hashlib.sha256((project / name).read_bytes()).hexdigest() != expected_hash:
                raise RuntimeError(f"Prepared project changed after snapshot: {name}")
            if name.startswith("src/chronochart/"):
                installed = package.parent / name.removeprefix("src/chronochart/")
                if hashlib.sha256(installed.read_bytes()).hexdigest() != expected_hash:
                    raise RuntimeError(f"Installed package differs from tested source: {name}")
        report["snapshot_manifest"] = "matched"
        report["installed_source_hashes"] = "matched"
        pins = [line.split("==") for name in ("requirements-build.txt", "requirements.lock.txt")
                for line in (project / name).read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
        mismatches = [name for name, version in pins if importlib.metadata.version(name) != version]
        if mismatches:
            raise RuntimeError(f"Pin mismatch: {mismatches}")
        report["environment"] = dict(python=sys.version, platform=platform.platform(), prefix=sys.prefix,
                                     package_file=str(package), pins_matched=len(pins))
        record, _, _ = call([sys.executable, "-c", "import sitecustomize; assert sitecustomize.NETWORK_DISABLED; import requests;\ntry: requests.get('https://example.invalid')\nexcept RuntimeError: pass\nelse: raise AssertionError('network guard missing')"], project, logs, "guard", env=env)
        report["commands"].append(record)
        seen = {}
        documents = [project / "README.md", project / "docs" / "cli_cache.md"]
        number = 0
        for document in documents:
            for line in shell_lines(document):
                category, args = command(line, project)
                evidence = dict(document=str(document.relative_to(project)), original=line, category=category)
                if category in ("setup-separate", "live-skipped"):
                    evidence["status"] = "verified-in-install-report" if category == "setup-separate" else "skipped"
                    report["commands"].append(evidence)
                    continue
                signature = (category, tuple(args))
                if signature in seen:
                    evidence.update(status="passed", same_execution_as=seen[signature])
                    report["commands"].append(evidence)
                    continue
                program = str(Path(sys.executable).parent / ("chronochart.exe" if os.name == "nt" else "chronochart")) if category == "console" else sys.executable
                argv = [program, *args] if category == "console" else [program, "-W", "error", *args]
                record, out, err = call(argv, project, logs, f"{number:02d}", env=env)
                if args[:2] == ["-m", "unittest"]:
                    match = re.search(r"Ran (\d+) tests", err)
                    if not match:
                        raise RuntimeError("Missing deterministic suite result")
                    report["deterministic_tests"] = int(match.group(1))
                evidence.update(record)
                report["commands"].append(evidence)
                seen[signature] = str(logs / f"{number:02d}.txt")
                number += 1
        if "deterministic_tests" not in report:
            raise RuntimeError("README did not run the full suite")
        # The guide's final command refreshes source data. Repeating its first
        # cached target proves the next job uses a HIT, not an old aggregate.
        cached = next(line for line in shell_lines(documents[1]) if "--cache-dir" in line and "--cache-refresh" not in line)
        _, args = command(cached, project)
        record, out, _ = call([sys.executable, "-W", "error", *args], project, logs, "cache-hit", env=env)
        if "cache: hit" not in out:
            raise RuntimeError("Expected a raw-cache hit after refresh")
        report["commands"].append(record)
        from chronochart.storage import load_export
        expected = dict(aapl10=9, aapl15=6, aapl3weeks=7, aapl2months=2, crypto45=32)
        report["exports"] = {}
        for name, count in expected.items():
            bundle = load_export(project / "data" / "exports" / f"{name}.json")
            if len(bundle.result.candles) != count or not bundle.provenance["synthetic"]:
                raise RuntimeError("README fixture export does not match its declared contract")
            report["exports"][name] = dict(candles=count, metadata_rows=len(bundle.result.metadata),
                                           synthetic=True, reload="passed")
        record, _, _ = call([sys.executable, "-m", "pip", "check"], project, logs, "pip-check", env=env)
        report["commands"].append(record)
        report["readme_sha256"] = hashlib.sha256((project / "README.md").read_bytes()).hexdigest()
        report["status"] = "passed"
    except Exception as exc:
        report["failure"] = str(exc)
        raise
    finally:
        report["finished_utc"] = timestamp()
        save(project.parent / f"verify-{mode}.json", report)
    print(f"Backend acceptance passed ({mode}): {report['deterministic_tests']} tests;5 fixture exports; live providers skipped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare").add_argument("--directory", required=True)
    for name in ("install", "verify"):
        command_parser = commands.add_parser(name)
        command_parser.add_argument("--project", required=True)
        command_parser.add_argument("--mode", choices=("editable", "wheel"), required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.directory)
    elif args.command == "install":
        install(args.project, args.mode)
    else:
        verify(args.project, args.mode)


if __name__ == "__main__":
    main()
