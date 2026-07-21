"""Deterministic permission classification for model-requested tools."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path


def resolve_user_path(cwd: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def detect_project_root(cwd: Path) -> Path:
    try:
        process = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
        if process.returncode == 0 and process.stdout.strip():
            return Path(process.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd.resolve()


def path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def argv_needs_interactive_tty(parts: list[str]) -> bool:
    if not parts:
        return False
    index = 0
    while index < len(parts) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", parts[index]):
        index += 1
    if index >= len(parts):
        return False
    command = Path(parts[index]).name
    if command in {"su", "passwd"}:
        return True
    if command == "sudo":
        sudo_args = parts[index + 1 :]
        return "-n" not in sudo_args and "--non-interactive" not in sudo_args
    return False


def command_needs_interactive_tty(command: str) -> bool:
    try:
        return argv_needs_interactive_tty(shlex.split(command))
    except ValueError:
        return False


def _safe_date_args(args: list[str]) -> bool:
    safe_exact = {
        "-u",
        "--utc",
        "--universal",
        "-R",
        "--rfc-email",
        "-I",
        "--iso-8601",
        "--help",
        "--version",
    }
    safe_prefixes = ("-I=", "--iso-8601=", "--rfc-3339=")
    return all(arg in safe_exact or arg.startswith(safe_prefixes) or arg.startswith("+") for arg in args)


def _safe_free_args(args: list[str]) -> bool:
    safe_exact = {
        "-b",
        "--bytes",
        "-k",
        "--kibi",
        "-m",
        "--mebi",
        "-g",
        "--gibi",
        "--tera",
        "-h",
        "--human",
        "--si",
        "-l",
        "--lohi",
        "-t",
        "--total",
        "-w",
        "--wide",
        "-v",
        "--committed",
        "--help",
        "-V",
        "--version",
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in safe_exact:
            index += 1
            continue
        if arg in {"-s", "--seconds", "-c", "--count"}:
            if index + 1 >= len(args) or not re.fullmatch(r"\d+(?:\.\d+)?", args[index + 1]):
                return False
            index += 2
            continue
        if arg.startswith(("--seconds=", "--count=")):
            _, value = arg.split("=", 1)
            if not re.fullmatch(r"\d+(?:\.\d+)?", value):
                return False
            index += 1
            continue
        return False
    return True


def _safe_df_args(args: list[str]) -> bool:
    safe_exact = {
        "-a",
        "--all",
        "-h",
        "--human-readable",
        "-H",
        "--si",
        "-i",
        "--inodes",
        "-k",
        "-l",
        "--local",
        "-P",
        "--portability",
        "-T",
        "--print-type",
        "--total",
        "--help",
        "--version",
    }
    safe_prefixes = ("-B", "--block-size=", "--output=", "-t", "--type=", "-x", "--exclude-type=")
    return all(arg in safe_exact or arg.startswith(safe_prefixes) for arg in args)


def _safe_lscpu_args(args: list[str]) -> bool:
    safe_exact = {
        "-a",
        "--all",
        "-b",
        "--online",
        "-c",
        "--offline",
        "-e",
        "--extended",
        "-J",
        "--json",
        "-p",
        "--parse",
        "-r",
        "--raw",
        "-x",
        "--hex",
        "-y",
        "--physical",
        "--output-all",
        "--help",
        "--version",
    }
    safe_prefixes = ("--extended=", "--parse=", "--caches=", "--hierarchic=")
    return all(arg in safe_exact or arg.startswith(safe_prefixes) for arg in args)


def _safe_pci_args(args: list[str]) -> bool:
    return all(re.fullmatch(r"-(?:[vmnktxbDP]+|h|V)", arg) for arg in args)


def _safe_usb_args(args: list[str]) -> bool:
    index = 0
    while index < len(args):
        arg = args[index]
        if re.fullmatch(r"-(?:[vthV]+)", arg):
            index += 1
            continue
        if arg in {"-s", "-d"}:
            if index + 1 >= len(args) or not re.fullmatch(r"[0-9A-Fa-f:*.-]+", args[index + 1]):
                return False
            index += 2
            continue
        return False
    return True


def _safe_ps_args(args: list[str]) -> bool:
    safe_forms = {
        (),
        ("a",),
        ("x",),
        ("ax",),
        ("aux",),
        ("-a",),
        ("-x",),
        ("-e",),
        ("-f",),
        ("-ef",),
        ("-A",),
        ("--forest",),
        ("-e", "--forest"),
        ("-ef", "--forest"),
        ("--help",),
        ("--version",),
    }
    return tuple(args) in safe_forms


def read_only_shell_argv(command: str) -> list[str] | None:
    """Return validated argv for a status-only command, or None when approval is required."""
    if re.search(r"[;&|><`$(){}\[\]\n\r]", command):
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if not parts or "=" in parts[0]:
        return None
    executable = parts[0]
    if executable.startswith((".", "~")) or "/" in executable or "\\" in executable:
        return None
    base = executable
    args = parts[1:]
    if base == "ollama":
        if args in (["list"], ["ps"]):
            return parts
        if len(args) == 2 and args[0] == "show" and not args[1].startswith("-"):
            return parts
        return None
    if base == "nvidia-smi":
        safe_flags = ("--query-", "--format=", "--id=", "-L", "-q", "-i", "-d", "-h", "--help")
        return parts if all(part.startswith(safe_flags) or part.isdigit() for part in args) else None
    if base == "hostname":
        safe_flags = {
            "-a",
            "--alias",
            "-d",
            "--domain",
            "-f",
            "--fqdn",
            "--long",
            "-i",
            "--ip-address",
            "-I",
            "--all-ip-addresses",
            "-s",
            "--short",
            "-y",
            "--yp",
            "--nis",
        }
        return parts if not args or all(part in safe_flags for part in args) else None
    if base == "date":
        return parts if _safe_date_args(args) else None
    if base == "free":
        return parts if _safe_free_args(args) else None
    if base == "df":
        return parts if _safe_df_args(args) else None
    if base == "lscpu":
        return parts if _safe_lscpu_args(args) else None
    if base == "lspci":
        return parts if _safe_pci_args(args) else None
    if base == "lsusb":
        return parts if _safe_usb_args(args) else None
    if base == "uname":
        safe = not args or all(
            re.fullmatch(
                r"-(?:[asnrvmpio]+|-all|-kernel-name|-nodename|-kernel-release|-kernel-version|-machine|-processor|-hardware-platform|-operating-system|-help|-version)",
                arg,
            )
            for arg in args
        )
        return parts if safe else None
    if base == "uptime":
        return (
            parts
            if all(arg in {"-p", "--pretty", "-s", "--since", "-h", "--help", "-V", "--version"} for arg in args)
            else None
        )
    if base in {"pwd", "whoami"}:
        return parts if not args else None
    if base == "ps":
        return parts if _safe_ps_args(args) else None
    if base == "id":
        return parts if all("/" not in arg and "\\" not in arg for arg in args) else None
    if base == "which":
        safe = all(
            arg in {"-a", "--all", "-s", "--skip-dot", "--skip-tilde", "--show-dot", "--show-tilde"}
            or re.fullmatch(r"[A-Za-z0-9_.+-]+", arg)
            for arg in args
        )
        return parts if args and safe else None
    return None


def is_read_only_shell_command(command: str) -> bool:
    """Recognize a narrow status-only shell command suitable for read-auto."""
    return read_only_shell_argv(command) is not None


def is_read_only_tool_call(call: dict[str, str]) -> bool:
    name = call.get("name")
    if name == "shell":
        return is_read_only_shell_command(call.get("cmd", ""))
    return name in {
        "read_file",
        "list_dir",
        "find_paths",
        "grep",
        "hardware_status",
        "search_project",
        "consult_specialist",
    }


def is_internal_coordinator_call(call: dict[str, str]) -> bool:
    return call.get("name") == "consult_specialist" and not str(call.get("path") or "").strip()


def is_auto_approvable_tool_call(
    call: dict[str, str],
    cwd: Path,
    project_root: Path | None = None,
) -> bool:
    """Return whether read-auto may execute this request without interaction."""
    name = str(call.get("name") or "")
    if name == "shell":
        return is_read_only_shell_command(call.get("cmd", ""))
    if name == "consult_specialist" and not str(call.get("path") or "").strip():
        return True
    if name == "hardware_status":
        return True
    if name not in {"read_file", "list_dir", "find_paths", "grep", "search_project", "consult_specialist"}:
        return False
    scope = project_root or cwd
    try:
        requested = resolve_user_path(scope, str(call.get("path") or "."))
    except (OSError, RuntimeError, ValueError):
        return False
    return path_within(requested, detect_project_root(scope))
