"""Console entry point and lifecycle commands."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import __version__
from .bootstrap import doctor, initialize
from .bridge import BridgeConfig, ComputeBridgeServer, load_or_create_bridge_token
from .catalog import BundleRecommendation, load_catalog, recommend_bundle, recommendation_set
from .compute import (
    LOCAL_OLLAMA_ENDPOINT,
    ComputeError,
    ComputeProbe,
    compute_token,
    endpoint_policy,
    probe_compute,
    provider_for_config,
    save_compute_token,
    stored_compute_token,
    validate_compute_endpoint,
)
from .config import ConfigError, default_config, load_config, save_config
from .hardware import GIB, HardwareProfile, detect_hardware, format_hardware
from .model_ops import (
    PullProgress,
    TransferCancelled,
    local_ollama_free_bytes,
    pull_model,
    remove_model,
    validate_model_name,
)
from .models import CAPABILITY_NAMES, load_hardware, load_registry, save_registry, set_capability_override
from .paths import PATHS, migrate_legacy_state
from .providers.ollama import OllamaError, OllamaProvider
from .updates import UpdateError, UpdateInfo, apply_update, check_for_update, format_update_command, update_command

COMPUTE_SERVICE_UNIT = "dairack-compute.service"


def _lifecycle_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dairack", add_help=False)
    parser.add_argument(
        "command",
        choices=("setup", "init", "doctor", "hardware", "models", "coordinator", "update", "connect", "serve"),
    )
    parser.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def _host_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"dairack {command}")
    parser.add_argument("--host", help="Ollama host, for example 127.0.0.1:11434")
    return parser


def _run_init(args: Sequence[str]) -> int:
    parser = _host_parser("init")
    parser.add_argument("--dry-run", action="store_true", help="inspect without writing configuration")
    options = parser.parse_args(args)
    try:
        result = initialize(PATHS, options.host, write=not options.dry_run)
    except (ConfigError, OllamaError) as exc:
        print(f"initialization failed: {exc}", file=sys.stderr)
        return 1
    print(result.report())
    return 0


def _run_doctor(args: Sequence[str]) -> int:
    options = _host_parser("doctor").parse_args(args)
    report = doctor(PATHS, options.host)
    print(report.render())
    return 0 if report.healthy else 1


def _connect_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dairack connect",
        description="Inspect or change the compute endpoint used for model inference.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="status",
        help="endpoint URL, local, remote, status, or test",
    )
    parser.add_argument("--name", default="", help="short display name for this compute server")
    parser.add_argument("--token-stdin", action="store_true", help="read a bearer token from standard input")
    parser.add_argument(
        "--allow-http",
        action="store_true",
        help="allow unencrypted HTTP outside loopback or a Tailscale address",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable connection status")
    return parser


def _compute_payload(probe: ComputeProbe, *, authenticated: bool) -> dict[str, Any]:
    accelerator = probe.hardware.primary_accelerator
    return {
        "name": probe.name,
        "endpoint": probe.endpoint,
        "mode": "local" if probe.local else "remote",
        "transport": probe.transport,
        "authenticated": authenticated,
        "ollama_version": probe.ollama_version,
        "bridge_version": probe.bridge_version,
        "hardware_verified": probe.hardware_verified,
        "cpu": probe.hardware.cpu_name if probe.hardware_verified else "not reported",
        "memory_gib": round(probe.hardware.memory_total_bytes / GIB, 1) if probe.hardware_verified else None,
        "accelerator": accelerator.name if accelerator else None,
        "accelerator_memory_gib": round(accelerator.memory_bytes / GIB, 1) if accelerator else None,
        "models": len(probe.models),
        "latency_ms": probe.latency_ms,
        "action_location": "client",
    }


def _print_compute_probe(probe: ComputeProbe, *, authenticated: bool, as_json: bool = False) -> None:
    payload = _compute_payload(probe, authenticated=authenticated)
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print("DAIRACK COMPUTE\n")
    print(f"  active       {payload['mode'].upper()} / {probe.name}")
    print(f"  endpoint     {probe.endpoint}")
    print(f"  transport    {probe.transport} / Ollama {probe.ollama_version} / {probe.latency_ms} ms")
    print(f"  auth         {'bearer token' if authenticated else 'endpoint managed'}")
    if probe.hardware_verified:
        print(f"  hardware     verified / {probe.hardware.cpu_name}")
        if probe.hardware.primary_accelerator:
            device = probe.hardware.primary_accelerator
            print(f"  accelerator  {device.name} / {device.memory_bytes / GIB:.1f} GiB")
    else:
        print("  hardware     not reported / backend settings remain automatic")
    print(f"  models       {len(probe.models)} available")
    print("  actions      this client / local approvals, files, shell, and chats")


def _probe_with_optional_prompt(endpoint: str, token: str, *, include_models: bool) -> tuple[ComputeProbe, str]:
    provider = OllamaProvider(endpoint, token) if token else OllamaProvider(endpoint)
    try:
        return probe_compute(provider, include_models=include_models), token
    except OllamaError as exc:
        if exc.status_code != 401 or not sys.stdin.isatty():
            raise
    try:
        entered = getpass.getpass("Compute token: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise ComputeError("compute token entry cancelled") from exc
    if not entered:
        raise ComputeError("a compute token is required")
    return probe_compute(OllamaProvider(endpoint, entered), include_models=include_models), entered


def _run_connect(args: Sequence[str]) -> int:
    options = _connect_parser().parse_args(args)
    try:
        config = load_config(PATHS)
    except ConfigError as exc:
        print(f"connection failed: {exc}", file=sys.stderr)
        return 1
    target = options.target.strip()
    if target in {"status", "test"}:
        endpoint = endpoint_policy(str(config.get("ollama_host") or LOCAL_OLLAMA_ENDPOINT)).endpoint
        token = compute_token(endpoint, PATHS)
        try:
            probe, token = _probe_with_optional_prompt(endpoint, token, include_models=True)
        except (ComputeError, OllamaError) as exc:
            print(f"compute endpoint unavailable: {exc}", file=sys.stderr)
            return 1
        _print_compute_probe(probe, authenticated=bool(token), as_json=options.json)
        return 0

    if target in {"local", "disconnect"}:
        endpoint = LOCAL_OLLAMA_ENDPOINT
    elif target == "remote":
        endpoint = str(config.get("remote_ollama_host") or "")
        if not endpoint:
            print("no remote compute endpoint has been saved", file=sys.stderr)
            return 2
    else:
        endpoint = target
    try:
        policy = validate_compute_endpoint(endpoint, allow_insecure=options.allow_http)
    except ComputeError as exc:
        print(f"connection rejected: {exc}", file=sys.stderr)
        return 2

    supplied_token = ""
    if options.token_stdin:
        supplied_token = sys.stdin.readline().strip()
        if not supplied_token:
            print("connection failed: no token was received on standard input", file=sys.stderr)
            return 2
    token = supplied_token or compute_token(policy.endpoint, PATHS)
    try:
        probe, token = _probe_with_optional_prompt(policy.endpoint, token, include_models=False)
    except (ComputeError, OllamaError) as exc:
        detail = str(exc)
        if isinstance(exc, OllamaError) and exc.status_code == 401 and not sys.stdin.isatty():
            detail += "; pipe the server token to `dairack connect URL --token-stdin`"
        print(f"connection failed: {detail}", file=sys.stderr)
        return 1

    previous_token = stored_compute_token(policy.endpoint, PATHS)
    token_changed = bool(token) and token != previous_token
    try:
        if token_changed:
            save_compute_token(policy.endpoint, token, PATHS)
        result = initialize(PATHS, policy.endpoint)
        display_name = options.name.strip()[:80]
        if display_name:
            updated = dict(result.config)
            updated["compute_name"] = display_name
            result.config = save_config(updated, PATHS)
        probe = replace(
            probe,
            name=str(result.config.get("compute_name") or probe.name),
            hardware=result.hardware,
            hardware_verified=result.registry.hardware_verified,
            models=tuple(record.descriptor for record in result.registry.models.values()),
        )
    except (ConfigError, ComputeError, OllamaError, OSError) as exc:
        if token_changed:
            save_compute_token(policy.endpoint, previous_token, PATHS)
        print(f"connection failed without changing the active endpoint: {exc}", file=sys.stderr)
        return 1
    _print_compute_probe(probe, authenticated=bool(token), as_json=options.json)
    return 0


def _serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dairack serve",
        description="Expose local Ollama as an authenticated Dairack compute service.",
    )
    parser.add_argument("--bind", default="127.0.0.1", help="listen address; loopback is the secure default")
    parser.add_argument("--port", type=int, default=11435, help="listen port (default: 11435)")
    parser.add_argument("--upstream", default=LOCAL_OLLAMA_ENDPOINT, help="local Ollama endpoint")
    parser.add_argument("--name", default="", help="server name shown to paired clients")
    parser.add_argument("--token-file", help="private token file; generated on first run")
    parser.add_argument("--show-token", action="store_true", help="print an existing pairing token")
    parser.add_argument("--no-auth", action="store_true", help="disable bridge auth; loopback only")
    parser.add_argument(
        "--tailscale", action="store_true", help="publish the loopback bridge to this tailnet over HTTPS"
    )
    service = parser.add_mutually_exclusive_group()
    service.add_argument(
        "--install-service",
        action="store_true",
        help="install and start a restartable Linux user service",
    )
    service.add_argument("--service-status", action="store_true", help="show Linux user-service status")
    service.add_argument("--remove-service", action="store_true", help="stop and remove the Linux user service")
    return parser


def _interactive_terminal() -> bool:
    streams = (sys.stdin, sys.stdout, sys.stderr)
    return all(bool(getattr(stream, "isatty", lambda: False)()) for stream in streams)


def _subprocess_output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace").strip()
    return (value or "").strip()


def _tailscale_serve(port: int) -> str:
    executable = shutil.which("tailscale")
    if not executable:
        raise ComputeError("Tailscale is not installed or is not on PATH")
    command = [executable, "serve", "--bg", "--yes", str(port)]
    if _interactive_terminal():
        print("TAILSCALE  Configuring the tailnet HTTPS endpoint...", flush=True)
        print("           First use may print an approval URL; open it to continue.\n", flush=True)
        completed = subprocess.run(command, check=False)
    else:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired as exc:
            detail = _subprocess_output(exc.stderr) or _subprocess_output(exc.stdout)
            message = (
                "Tailscale Serve needs interactive first-use approval; run "
                f"`tailscale serve --bg --yes {port}` in a terminal, open the URL it prints, then retry"
            )
            if detail:
                message = f"{message}\n{detail}"
            raise ComputeError(message) from exc
    if completed.returncode:
        detail = (
            _subprocess_output(getattr(completed, "stderr", None))
            or _subprocess_output(getattr(completed, "stdout", None))
            or f"exit {completed.returncode}"
        )
        raise ComputeError(f"could not configure Tailscale Serve: {detail}")
    try:
        status = subprocess.run(
            [executable, "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return "the HTTPS URL shown by `tailscale serve status`"
    try:
        payload = json.loads(status.stdout) if status.returncode == 0 else {}
    except json.JSONDecodeError:
        payload = {}
    self_status = payload.get("Self") if isinstance(payload, dict) else {}
    dns_name = str(self_status.get("DNSName") or "").rstrip(".") if isinstance(self_status, dict) else ""
    return f"https://{dns_name}" if dns_name else "the HTTPS URL shown by `tailscale serve status`"


def _user_service_path() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".config"
    return root / "systemd" / "user" / COMPUTE_SERVICE_UNIT


def _systemctl_user(*args: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("systemctl")
    if not executable:
        raise ComputeError("systemctl is not available; run the compute bridge in foreground mode")
    return subprocess.run(
        [executable, "--user", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _service_error(result: subprocess.CompletedProcess[str]) -> str:
    return _subprocess_output(result.stderr) or _subprocess_output(result.stdout) or f"exit {result.returncode}"


def _compute_service_command(options: argparse.Namespace, token_file: Path, upstream: str) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "dairack",
        "serve",
        "--bind",
        str(options.bind),
        "--port",
        str(options.port),
        "--upstream",
        upstream,
        "--token-file",
        str(token_file),
    ]
    if options.name:
        command.extend(("--name", str(options.name)))
    if options.no_auth:
        command.append("--no-auth")
    return command


def _install_compute_service(options: argparse.Namespace, token_file: Path, upstream: str) -> None:
    if sys.platform != "linux":
        raise ComputeError("automatic service installation currently requires Linux with systemd")
    unit_path = _user_service_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    command = shlex.join(_compute_service_command(options, token_file, upstream))
    unit = (
        "[Unit]\n"
        "Description=Dairack compute bridge\n"
        "Wants=network-online.target\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={command}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "Environment=PYTHONUNBUFFERED=1\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    temporary = unit_path.with_suffix(".service.tmp")
    temporary.write_text(unit, encoding="utf-8")
    temporary.replace(unit_path)
    for args in (("daemon-reload",), ("enable", COMPUTE_SERVICE_UNIT), ("restart", COMPUTE_SERVICE_UNIT)):
        result = _systemctl_user(*args)
        if result.returncode:
            raise ComputeError(f"could not {' '.join(args)}: {_service_error(result)}")


def _compute_service_status() -> int:
    try:
        result = _systemctl_user("status", COMPUTE_SERVICE_UNIT, "--no-pager")
    except (ComputeError, subprocess.TimeoutExpired) as exc:
        print(f"service status failed: {exc}", file=sys.stderr)
        return 1
    output = result.stdout.strip() or result.stderr.strip()
    print(output or f"{COMPUTE_SERVICE_UNIT}: no status returned")
    return 0 if result.returncode == 0 else 1


def _remove_compute_service() -> int:
    try:
        result = _systemctl_user("disable", "--now", COMPUTE_SERVICE_UNIT)
        if result.returncode and "not loaded" not in _service_error(result).lower():
            raise ComputeError(_service_error(result))
        _user_service_path().unlink(missing_ok=True)
        reload_result = _systemctl_user("daemon-reload")
        if reload_result.returncode:
            raise ComputeError(_service_error(reload_result))
    except (ComputeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"service removal failed: {exc}", file=sys.stderr)
        return 1
    print("Dairack compute service removed.")
    print("Tailscale Serve remains configured; disable it separately if it is no longer needed.")
    return 0


def _linger_enabled() -> bool | None:
    executable = shutil.which("loginctl")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "show-user", getpass.getuser(), "-p", "Linger", "--value"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    value = result.stdout.strip().lower()
    return True if value == "yes" else False if value == "no" else None


def _run_serve(args: Sequence[str]) -> int:
    options = _serve_parser().parse_args(args)
    if options.service_status:
        return _compute_service_status()
    if options.remove_service:
        return _remove_compute_service()
    if options.tailscale and options.bind not in {"127.0.0.1", "::1", "localhost"}:
        print("serve failed: --tailscale requires the bridge to remain bound to loopback", file=sys.stderr)
        return 2
    token_file = Path(options.token_file).expanduser() if options.token_file else PATHS.compute_bridge_token_file
    server: ComputeBridgeServer | None = None
    try:
        try:
            token_existed = bool(token_file.read_text(encoding="ascii").strip())
        except FileNotFoundError:
            token_existed = False
        token = "" if options.no_auth else load_or_create_bridge_token(token_file)
        upstream = endpoint_policy(options.upstream).endpoint
        OllamaProvider(upstream).version()
        if options.install_service:
            public_endpoint = (
                _tailscale_serve(options.port) if options.tailscale else f"http://{options.bind}:{options.port}"
            )
            _install_compute_service(options, token_file, upstream)
            print("DAIRACK COMPUTE SERVICE\n")
            print(f"  endpoint     {public_endpoint}")
            print(f"  upstream     {upstream}")
            print("  lifecycle    systemd user service / restart on failure")
            print(f"  unit         {_user_service_path()}")
            print(f"  access       {'bearer token' if token else 'loopback only / no token'}")
            if token:
                print(f"  token file   {token_file}")
                if options.show_token or not token_existed:
                    print(f"\nPAIR TOKEN\n{token}")
                print(f"\nOn the client:  dairack connect {public_endpoint}")
            if _linger_enabled() is False:
                print(
                    f"\nNOTICE  Keep the service running after logout with: sudo loginctl enable-linger {getpass.getuser()}"
                )
            print("\nREADY  Manage with `dairack serve --service-status` or `dairack serve --remove-service`.")
            return 0
        server = ComputeBridgeServer(
            BridgeConfig(
                bind=options.bind,
                port=options.port,
                upstream=upstream,
                token=token,
                node_name=options.name,
            )
        )
        port = int(server.server_address[1])
        public_endpoint = _tailscale_serve(port) if options.tailscale else f"http://{options.bind}:{port}"
    except KeyboardInterrupt:
        if server is not None:
            server.server_close()
        print("\nSTOPPED")
        return 130
    except (ComputeError, OllamaError, OSError, subprocess.TimeoutExpired) as exc:
        if server is not None:
            server.server_close()
        print(f"serve failed: {exc}", file=sys.stderr)
        return 1

    print("DAIRACK COMPUTE SERVICE\n")
    print(f"  endpoint     {public_endpoint}")
    print(f"  upstream     {upstream}")
    print(f"  access       {'bearer token' if token else 'loopback only / no token'}")
    print("  surface      model inference + read-only hardware metadata")
    print("  excluded     files, shell, approvals, chats, and checkpoints")
    if token:
        print(f"  token file   {token_file}")
        if options.show_token or not token_existed:
            print(f"\nPAIR TOKEN\n{token}")
        else:
            print("\nPAIR TOKEN  existing / restart with --show-token to display")
        print(f"\nOn the client:  dairack connect {public_endpoint}")
    print("\nREADY  Press Ctrl+C to stop the foreground service.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nSTOPPED")
    finally:
        server.server_close()
    return 0


def _model_name(raw: str, names: Sequence[str]) -> str:
    if raw in names:
        return raw
    matches = [name for name in names if name.startswith(raw)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"model not found: {raw}")
    raise ValueError(f"ambiguous model name: {raw}; matches {', '.join(matches)}")


def _models_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dairack models")
    subparsers = parser.add_subparsers(dest="action")
    refresh = subparsers.add_parser("refresh", help="rediscover Ollama models and hardware profiles")
    refresh.add_argument("--host")
    inspect = subparsers.add_parser("inspect", help="show one generated model profile")
    inspect.add_argument("model")
    set_parser = subparsers.add_parser("set", help="override a capability or runtime setting")
    set_parser.add_argument("model")
    set_parser.add_argument("field", help="capability name, num_ctx, num_batch, num_thread, or think")
    set_parser.add_argument("value")
    reset = subparsers.add_parser("reset", help="remove all overrides for a model")
    reset.add_argument("model")
    recommend = subparsers.add_parser("recommend", help="show hardware-aware optional setup profiles")
    recommend.add_argument("--profile", choices=("minimal", "balanced", "complete"))
    recommend.add_argument("--json", action="store_true", help="emit machine-readable output")
    pull = subparsers.add_parser("pull", aliases=["install"], help="install a model, or update the same tag")
    pull.add_argument("model")
    pull.add_argument("--host")
    update = subparsers.add_parser("update", help="re-pull one installed tag or every installed tag")
    update.add_argument("model", nargs="?")
    update.add_argument("--all", action="store_true", help="update every installed model")
    update.add_argument("--yes", action="store_true", help="skip the confirmation for --all")
    update.add_argument("--host")
    remove = subparsers.add_parser("remove", aliases=["rm"], help="remove an installed model")
    remove.add_argument("model")
    remove.add_argument("--yes", action="store_true", help="skip confirmation")
    remove.add_argument("--host")
    return parser


def _size_human(value: int) -> str:
    if value >= GIB:
        return f"{value / GIB:.1f} GiB"
    if value >= 1024**2:
        return f"{value / 1024**2:.1f} MiB"
    return f"{value / 1024:.1f} KiB"


def _provider(host: str | None = None) -> OllamaProvider:
    config = load_config(PATHS)
    return provider_for_config(config, PATHS, host=host)


def _confirm(prompt: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in {"y", "yes"}


def _progress_printer() -> tuple[Callable[[PullProgress], None], Callable[[], None]]:
    interactive = sys.stdout.isatty()
    previous_status = ""
    printed = False

    def update(progress: PullProgress) -> None:
        nonlocal previous_status, printed
        if progress.percent is None:
            detail = progress.status
        else:
            detail = (
                f"{progress.status:<18} {progress.percent * 100:5.1f}%  "
                f"{_size_human(progress.completed)} / {_size_human(progress.total)}"
            )
        if interactive:
            print(f"\r  {detail:<76}", end="", flush=True)
            printed = True
        elif progress.status != previous_status:
            print(f"  {detail}")
        previous_status = progress.status

    def finish() -> None:
        if interactive and printed:
            print()

    return update, finish


def _pull_many(models: Sequence[str], host: str | None = None) -> int:
    try:
        names = list(dict.fromkeys(validate_model_name(model) for model in models))
        provider = _provider(host)
        provider.version()
    except (ConfigError, OllamaError, ValueError) as exc:
        print(f"model install failed: {exc}", file=sys.stderr)
        return 1
    catalog = load_catalog()
    results = []
    for index, name in enumerate(names, 1):
        known = catalog.models.get(name)
        size = f" / approximately {known.download_gib:.1f} GiB" if known else ""
        prefix = f"[{index}/{len(names)}] " if len(names) > 1 else ""
        print(f"{prefix}Installing or updating {name}{size}")
        update, finish = _progress_printer()
        try:
            results.append(pull_model(provider, name, on_progress=update))
        except (OllamaError, TransferCancelled) as exc:
            finish()
            print(f"model install failed: {exc}", file=sys.stderr)
            return 1
        finish()
    try:
        initialize(PATHS, host)
    except (ConfigError, OllamaError) as exc:
        print(f"models installed, but registry refresh failed: {exc}", file=sys.stderr)
        return 1
    for result in results:
        print(f"Ready: {result.model} ({result.elapsed:.1f}s)")
    return 0


def _pull_one(model: str, host: str | None = None) -> int:
    return _pull_many([model], host)


def _recommendation_payload(recommendation: BundleRecommendation) -> dict[str, Any]:
    return {
        "id": recommendation.bundle.id,
        "label": recommendation.bundle.label,
        "summary": recommendation.bundle.summary,
        "models_to_install": [model.name for model in recommendation.models],
        "download_gib": round(recommendation.download_gib, 2),
        "covered_roles": recommendation.covered_roles,
        "missing_roles": list(recommendation.missing_roles),
    }


def _render_recommendations(profile: str | None = None, as_json: bool = False) -> int:
    registry = load_registry(PATHS)
    hardware = load_hardware(PATHS) if registry else detect_hardware()
    if registry and not registry.hardware_verified:
        if as_json:
            print(
                json.dumps(
                    {
                        "hardware": None,
                        "hardware_verified": False,
                        "recommendations": [],
                        "detail": "the active remote Ollama endpoint does not report hardware",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print("MODEL SETUP PROFILES\n")
            print("The active remote Ollama endpoint does not report hardware.")
            print(
                "Install any compatible model directly, or use the Dairack compute bridge for fitted recommendations."
            )
        return 0
    values = (recommend_bundle(profile, hardware, registry),) if profile else recommendation_set(hardware, registry)
    if as_json:
        print(
            json.dumps(
                {
                    "hardware": hardware.to_dict(),
                    "catalog_updated_at": load_catalog().updated_at,
                    "recommendations": [_recommendation_payload(value) for value in values],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    print("MODEL SETUP PROFILES")
    print(f"{hardware.memory_total_bytes / GIB:.1f} GiB RAM", end="")
    if hardware.primary_accelerator:
        print(
            f" / {hardware.primary_accelerator.name} ({hardware.primary_accelerator.memory_bytes / GIB:.1f} GiB)",
            end="",
        )
    print("\n")
    for value in values:
        print(f"{value.bundle.label.upper()}  {value.bundle.summary}")
        if value.models:
            for model in value.models:
                print(f"  install  {model.name:<30} approximately {model.download_gib:.1f} GiB")
            print(f"  new download: approximately {value.download_gib:.1f} GiB")
        else:
            print("  ready with the installed model set")
        for role, model in value.covered_roles.items():
            print(f"  {role:<9} {model}")
        if value.missing_roles:
            print(f"  unavailable on detected hardware: {', '.join(value.missing_roles)}")
        print()
    print("Recommendations are optional. Any installed Ollama chat model can be selected directly.")
    return 0


def _remove_installed_model(model: str, host: str | None, assume_yes: bool) -> int:
    try:
        provider = _provider(host)
        installed = provider.list_models()
        name = _model_name(model, [item.name for item in installed])
    except (ConfigError, OllamaError, ValueError) as exc:
        print(f"model removal failed: {exc}", file=sys.stderr)
        return 1
    descriptor = next(item for item in installed if item.name == name)
    prompt = f"Remove {name} ({_size_human(descriptor.size)}) from the active compute endpoint?"
    if not _confirm(prompt, assume_yes):
        print("Model was not removed. Pass --yes for non-interactive use.", file=sys.stderr)
        return 2
    try:
        remove_model(provider, name)
        initialize(PATHS, host)
    except (ConfigError, OllamaError, ValueError) as exc:
        print(f"model removal failed: {exc}", file=sys.stderr)
        return 1
    print(f"Removed {name}")
    return 0


def _update_installed_models(model: str | None, update_all: bool, assume_yes: bool, host: str | None) -> int:
    if bool(model) == bool(update_all):
        print("choose one installed model or pass --all", file=sys.stderr)
        return 2
    try:
        provider = _provider(host)
        installed = provider.list_models()
    except (ConfigError, OllamaError) as exc:
        print(f"model update failed: {exc}", file=sys.stderr)
        return 1
    if not installed:
        print("No installed models to update.", file=sys.stderr)
        return 1
    if update_all:
        names = [item.name for item in installed]
        if not _confirm(f"Check and re-pull all {len(names)} installed model tags?", assume_yes):
            print("Update cancelled. Pass --yes for non-interactive use.", file=sys.stderr)
            return 2
    else:
        try:
            names = [_model_name(str(model), [item.name for item in installed])]
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    return _pull_many(names, host)


def _setup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dairack setup")
    parser.add_argument("--host", help="Ollama host, for example 127.0.0.1:11434")
    parser.add_argument("--profile", choices=("minimal", "balanced", "complete", "manual"))
    parser.add_argument("--model", action="append", default=[], help="install any Ollama model; may be repeated")
    parser.add_argument("--policy", choices=("adaptive", "quality", "efficient"), default="adaptive")
    parser.add_argument("--direct", action="store_true", help="start in direct-model mode instead of Coordinator")
    parser.add_argument("--no-pull", action="store_true", help="show and save choices without downloading")
    parser.add_argument("--yes", action="store_true", help="accept the displayed download plan")
    return parser


def _interactive_setup_choice(
    recommendations: Sequence[BundleRecommendation], has_models: bool
) -> tuple[str, list[str]]:
    print("SETUP PROFILE\n")
    for index, recommendation in enumerate(recommendations, 1):
        marker = "  RECOMMENDED" if recommendation.bundle.id == "balanced" else ""
        download = (
            f"approximately {recommendation.download_gib:.1f} GiB new"
            if recommendation.models
            else "ready with installed models"
        )
        print(f"  {index}. {recommendation.bundle.label:<10} {download}{marker}")
        print(f"     {recommendation.bundle.summary}")
    print("  4. Keep current models" if has_models else "  4. No recommended download")
    print("  5. Install any Ollama model")
    try:
        raw = input("\nChoose [2]: ").strip() or "2"
    except (EOFError, KeyboardInterrupt):
        print()
        return "manual", []
    if raw in {"1", "2", "3"}:
        recommendation = recommendations[int(raw) - 1]
        return recommendation.bundle.id, [model.name for model in recommendation.models]
    if raw == "5":
        try:
            model = input("Ollama model name: ").strip()
            return "manual", [validate_model_name(model)] if model else []
        except (EOFError, KeyboardInterrupt, ValueError):
            print()
            return "manual", []
    return "manual", []


def _download_preflight(models: Sequence[str], hardware: HardwareProfile, host: str | None) -> tuple[float, str]:
    catalog = load_catalog()
    total = sum(catalog.models[name].download_gib for name in models if name in catalog.models)
    warning = ""
    if total:
        free = local_ollama_free_bytes(str(host or "127.0.0.1:11434"))
        free_gib = free / GIB if free is not None else 0.0
        if free_gib and free_gib < total * 1.15:
            warning = f"Only {free_gib:.1f} GiB is free; this plan needs roughly {total:.1f} GiB plus working space."
    if hardware.memory_total_bytes == 0:
        warning = (
            warning + " " if warning else ""
        ) + "System memory could not be measured; fit estimates are uncertain."
    return total, warning


def _run_setup(args: Sequence[str]) -> int:
    options = _setup_parser().parse_args(args)
    try:
        result = initialize(PATHS, options.host)
    except (ConfigError, OllamaError) as exc:
        print("Dairack setup could not reach Ollama.", file=sys.stderr)
        print(f"{exc}", file=sys.stderr)
        print("Install and start Ollama, then rerun `dairack setup`: https://ollama.com/download", file=sys.stderr)
        return 1

    print("DAIRACK SETUP\n")
    print(format_hardware(result.hardware))
    print(f"\nOllama {result.ollama_version} / {len(result.registry.models)} installed model(s)\n")
    recommendations = recommendation_set(result.hardware, result.registry)
    profile = options.profile
    models = list(options.model)
    if not profile and not models:
        if not sys.stdin.isatty():
            _render_recommendations()
            print("Run again with --profile, --model, or an interactive terminal.", file=sys.stderr)
            return 2
        profile, models = _interactive_setup_choice(recommendations, bool(result.registry.models))
    elif profile and profile != "manual" and not models:
        recommendation = recommend_bundle(profile, result.hardware, result.registry)
        models = [model.name for model in recommendation.models]

    try:
        models = list(dict.fromkeys(validate_model_name(model) for model in models))
    except ValueError as exc:
        print(f"invalid model selection: {exc}", file=sys.stderr)
        return 2
    total, warning = _download_preflight(models, result.hardware, options.host or result.config.get("ollama_host"))
    if models:
        print("DOWNLOAD PLAN")
        catalog = load_catalog()
        for model in models:
            known = catalog.models.get(model)
            estimate = f"approximately {known.download_gib:.1f} GiB" if known else "size reported by Ollama"
            print(f"  {model:<34} {estimate}")
        if total:
            print(f"  {'TOTAL':<34} approximately {total:.1f} GiB")
        if warning:
            print(f"\nWARNING  {warning}")
        if options.no_pull:
            print("\nDownloads skipped by --no-pull.")
        elif not _confirm("Install this model plan?", options.yes):
            print("Setup cancelled before downloading models.")
            return 2
        elif _pull_many(models, options.host):
            return 1
    elif not result.registry.models:
        print("No model selected. Dairack is configured, but a model is required before chatting.")

    config = load_config(PATHS)
    config["model_mode"] = "direct" if options.direct else "orchestrator"
    config["orchestrator_policy"] = options.policy
    config["permission_mode"] = "ask"
    save_config(config, PATHS)
    final_registry = load_registry(PATHS)
    count = len(final_registry.models) if final_registry else 0
    print("\nSETUP COMPLETE")
    print(f"  models       {count}")
    print(f"  mode         {'direct' if options.direct else 'Coordinator / ' + options.policy}")
    print("  permissions  ask before actions")
    print("\nRun `dairack` to start.")
    return 0


COORDINATOR_FIELDS = {
    "planning": "orchestrator_planning",
    "review": "orchestrator_review",
    "delegation": "orchestrator_delegation",
    "semantic": "orchestrator_semantic_routing",
    "semantic-routing": "orchestrator_semantic_routing",
    "learning": "coordinator_learning",
}
COORDINATOR_ROLES = ("general", "coding", "agent", "reasoning", "research", "vision", "planner", "reviewer")


def _coordinator_status(config: dict[str, Any]) -> str:
    registry = load_registry(PATHS)
    count = len(registry.models) if registry else 0
    enabled = config.get("model_mode") == "orchestrator"
    lines = [
        "COORDINATOR",
        f"  mode       {'enabled' if enabled else 'disabled / direct model'}",
        f"  policy     {config.get('orchestrator_policy', 'adaptive')}",
        f"  models     {count} available",
    ]
    for label, key in COORDINATOR_FIELDS.items():
        if label == "semantic-routing":
            continue
        lines.append(f"  {label:<10} {'on' if config.get(key, True) else 'off'}")
    preferences = config.get("coordinator_role_preferences")
    if isinstance(preferences, dict) and preferences:
        lines.append("\n  ROLE PREFERENCES / SOFT")
        for role, model in preferences.items():
            lines.append(f"  {role:<10} {model}")
    if count < 2:
        lines.append(
            "\nCoordinator remains usable with one model; specialist handoffs activate when alternatives exist."
        )
    return "\n".join(lines)


def _coordinator_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dairack coordinator")
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("show", help="show the active coordinator configuration")
    subparsers.add_parser("enable", help="use adaptive model coordination")
    subparsers.add_parser("disable", help="use the selected model directly")
    policy = subparsers.add_parser("policy", help="set the quality/latency policy")
    policy.add_argument("value", choices=("adaptive", "quality", "efficient"))
    setting = subparsers.add_parser("set", help="enable or disable an advanced coordinator stage")
    setting.add_argument("feature", choices=tuple(COORDINATOR_FIELDS))
    setting.add_argument("value", choices=("on", "off"))
    prefer = subparsers.add_parser("prefer", help="set a soft model preference for one coordinator role")
    prefer.add_argument("role", choices=COORDINATOR_ROLES)
    prefer.add_argument("model", help="installed model name, or auto to clear")
    subparsers.add_parser("reset", help="restore adaptive coordinator defaults")
    return parser


def _run_coordinator(args: Sequence[str]) -> int:
    options = _coordinator_parser().parse_args(args)
    try:
        config = load_config(PATHS)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 1
    if options.action == "enable":
        config["model_mode"] = "orchestrator"
    elif options.action == "disable":
        config["model_mode"] = "direct"
    elif options.action == "policy":
        config["orchestrator_policy"] = options.value
        config["model_mode"] = "orchestrator"
    elif options.action == "set":
        config[COORDINATOR_FIELDS[options.feature]] = options.value == "on"
    elif options.action == "prefer":
        preferences = config.setdefault("coordinator_role_preferences", {})
        if options.model.lower() in {"auto", "none", "off"}:
            preferences.pop(options.role, None)
        else:
            registry = load_registry(PATHS)
            if not registry:
                print("Model registry is not initialized. Run `dairack init`.", file=sys.stderr)
                return 1
            try:
                preferences[options.role] = _model_name(options.model, list(registry.models))
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
    elif options.action == "reset":
        defaults = default_config()
        for key in (
            "model_mode",
            "orchestrator_policy",
            "coordinator_role_preferences",
            *set(COORDINATOR_FIELDS.values()),
        ):
            config[key] = defaults[key]
    if options.action and options.action != "show":
        try:
            save_config(config, PATHS)
        except ConfigError as exc:
            print(f"configuration error: {exc}", file=sys.stderr)
            return 1
    print(_coordinator_status(config))
    return 0


def _update_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dairack update")
    subparsers = parser.add_subparsers(dest="action")
    check = subparsers.add_parser("check", help="check the configured release channel")
    check.add_argument("--force", action="store_true", help="ignore the cached result")
    check.add_argument("--url", help="temporarily use another HTTPS release endpoint")
    apply_parser = subparsers.add_parser("apply", help="install the newest release after confirmation")
    apply_parser.add_argument("--force", action="store_true", help="refresh release metadata first")
    apply_parser.add_argument("--yes", action="store_true", help="skip the confirmation")
    apply_parser.add_argument("--url", help="temporarily use another HTTPS release endpoint")
    channel = subparsers.add_parser("channel", help="show, configure, or disable update discovery")
    channel.add_argument("value", nargs="?", help="HTTPS manifest/PyPI JSON URL, or off")
    channel.add_argument("--interval", type=int, help="cache interval in hours (1-720)")
    return parser


def _print_update(info: UpdateInfo) -> None:
    if info.available:
        command = format_update_command(update_command(info.latest_version))
        print("DAIRACK UPDATE AVAILABLE")
        print(f"  current  {info.current_version}")
        print(f"  latest   {info.latest_version}")
        print(f"  command  {command}")
        if info.notes_url:
            print(f"  notes    {info.notes_url}")
    else:
        print(f"Dairack {info.current_version} is current (channel latest: {info.latest_version}).")
    if info.from_cache:
        print("  source   cached release metadata")


def _run_update(args: Sequence[str]) -> int:
    options = _update_parser().parse_args(args)
    try:
        config = load_config(PATHS)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 1

    if options.action == "channel":
        if options.value:
            if options.value.lower() in {"off", "none", "disable"}:
                config["check_updates"] = False
                config["update_index_url"] = ""
            else:
                config["check_updates"] = True
                config["update_index_url"] = options.value
        if options.interval is not None:
            config["update_check_interval_hours"] = options.interval
        if options.value or options.interval is not None:
            try:
                config = save_config(config, PATHS)
            except ConfigError as exc:
                print(f"configuration error: {exc}", file=sys.stderr)
                return 2
        enabled = bool(config.get("check_updates")) and bool(config.get("update_index_url"))
        print("UPDATE CHANNEL")
        print(f"  state     {'enabled' if enabled else 'disabled'}")
        print(f"  endpoint  {config.get('update_index_url') or 'not configured'}")
        print(f"  interval  {config.get('update_check_interval_hours', 24)} hours")
        return 0

    source_url = str(getattr(options, "url", None) or config.get("update_index_url") or "")
    force = bool(getattr(options, "force", False))
    try:
        info = check_for_update(
            __version__,
            source_url,
            paths=PATHS,
            force=force,
            max_age_seconds=float(config.get("update_check_interval_hours", 24)) * 3600,
        )
    except UpdateError as exc:
        print(f"update check failed: {exc}", file=sys.stderr)
        if not source_url:
            print("Configure one with `dairack update channel <https-url>`.", file=sys.stderr)
        return 1
    _print_update(info)
    if options.action != "apply" or not info.available:
        return 0
    command = format_update_command(update_command(info.latest_version))
    if not _confirm(f"Run `{command}`? Existing chats and configuration are preserved.", options.yes):
        print("Update cancelled. Pass --yes for non-interactive use.", file=sys.stderr)
        return 2
    try:
        result = apply_update(info)
    except UpdateError as exc:
        print(f"update failed: {exc}", file=sys.stderr)
        return 1
    if result.returncode:
        print(f"update command failed with exit {result.returncode}", file=sys.stderr)
        return result.returncode
    print(f"Updated to Dairack {info.latest_version}. Restart `dairack` to use the new release.")
    return 0


def _render_models() -> int:
    registry = load_registry(PATHS)
    if not registry:
        print("Model registry is not initialized. Run `dairack init`.")
        return 1
    print("COMPUTE MODEL REGISTRY")
    for record in registry.models.values():
        capability = record.effective_capability()
        runtime = record.effective_runtime()
        options = runtime.get("options") if isinstance(runtime.get("options"), dict) else {}
        override = " / configured" if record.override else ""
        print(f"\n{record.descriptor.name}{override}")
        print(f"  {record.role} | {runtime.get('fit', 'unknown')} fit")
        print(
            f"  code {capability.code:.2f}  agent {capability.agent:.2f}  reason {capability.reasoning:.2f}  "
            f"vision {capability.vision:.2f}  efficiency {capability.efficiency:.2f}"
        )
        print(f"  ctx {runtime.get('num_ctx')}  batch {options.get('num_batch')}  threads {options.get('num_thread')}")
    return 0


def _set_model_override(model: str, field: str, raw_value: str) -> int:
    registry = load_registry(PATHS)
    if not registry:
        print("Model registry is not initialized. Run `dairack init`.", file=sys.stderr)
        return 1
    try:
        name = _model_name(model, list(registry.models))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    record = registry.models[name]
    field = field.lower().replace("-", "_")
    if field in CAPABILITY_NAMES:
        try:
            value: Any = float(raw_value)
        except ValueError:
            print("capabilities require a value from 0.0 to 1.0", file=sys.stderr)
            return 2
        if not 0.0 <= value <= 1.0:
            print("capabilities require a value from 0.0 to 1.0", file=sys.stderr)
            return 2
        set_capability_override(name, field, value, PATHS)
        print(f"Updated {name}: {field}={raw_value}")
        return 0
    elif field in {"num_ctx", "num_batch", "num_thread"}:
        try:
            value = int(raw_value)
        except ValueError:
            print(f"{field} requires an integer", file=sys.stderr)
            return 2
        if value <= 0:
            print(f"{field} must be positive", file=sys.stderr)
            return 2
        if field == "num_ctx":
            record.override.setdefault("runtime", {})[field] = value
        else:
            record.override.setdefault("runtime", {}).setdefault("options", {})[field] = value
    elif field == "think":
        if raw_value.lower() not in {"on", "off", "true", "false"}:
            print("think requires on or off", file=sys.stderr)
            return 2
        record.override.setdefault("runtime", {})[field] = raw_value.lower() in {"on", "true"}
    else:
        choices = ", ".join((*CAPABILITY_NAMES, "num_ctx", "num_batch", "num_thread", "think"))
        print(f"unknown field {field}; choose {choices}", file=sys.stderr)
        return 2
    save_registry(registry, PATHS)
    print(f"Updated {name}: {field}={raw_value}")
    return 0


def _run_models(args: Sequence[str]) -> int:
    options = _models_parser().parse_args(args)
    if not options.action:
        return _render_models()
    if options.action == "recommend":
        return _render_recommendations(options.profile, options.json)
    if options.action in {"pull", "install"}:
        return _pull_one(options.model, options.host)
    if options.action == "update":
        return _update_installed_models(options.model, options.all, options.yes, options.host)
    if options.action in {"remove", "rm"}:
        return _remove_installed_model(options.model, options.host, options.yes)
    if options.action == "refresh":
        try:
            result = initialize(PATHS, options.host)
        except (ConfigError, OllamaError) as exc:
            print(f"model refresh failed: {exc}", file=sys.stderr)
            return 1
        print(result.report())
        return 0
    registry = load_registry(PATHS)
    if not registry:
        print("Model registry is not initialized. Run `dairack init`.", file=sys.stderr)
        return 1
    try:
        name = _model_name(options.model, list(registry.models))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if options.action == "inspect":
        print(json.dumps(registry.models[name].to_dict(), indent=2, sort_keys=True))
        return 0
    if options.action == "set":
        return _set_model_override(name, options.field, options.value)
    if options.action == "reset":
        registry.models[name].override = {}
        save_registry(registry, PATHS)
        print(f"Reset overrides for {name}")
        return 0
    return 2


def _run_runtime(args: Sequence[str]) -> int:
    from . import runtime

    original = sys.argv
    try:
        sys.argv = [original[0], *args]
        return runtime.main()
    finally:
        sys.argv = original


def main(argv: Sequence[str] | None = None) -> int:
    migration = migrate_legacy_state(PATHS)
    if migration.changed and sys.stderr.isatty():
        print("Migrated existing local state to Dairack.", file=sys.stderr)
    if migration.errors:
        print("State migration could not read every legacy item:", file=sys.stderr)
        for error in migration.errors:
            print(f"  {error}", file=sys.stderr)
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"--version", "-V"}:
        print(f"dairack {__version__}")
        return 0
    if args and args[0] == "ask":
        return _run_runtime(args[1:])
    if args and args[0] in {
        "setup",
        "init",
        "doctor",
        "hardware",
        "models",
        "coordinator",
        "update",
        "connect",
        "serve",
    }:
        parsed = _lifecycle_parser().parse_args(args)
        if parsed.command == "setup":
            return _run_setup(parsed.args)
        if parsed.command == "init":
            return _run_init(parsed.args)
        if parsed.command == "doctor":
            return _run_doctor(parsed.args)
        if parsed.command == "connect":
            return _run_connect(parsed.args)
        if parsed.command == "serve":
            return _run_serve(parsed.args)
        if parsed.command == "hardware":
            if parsed.args:
                print("usage: dairack hardware", file=sys.stderr)
                return 2
            print(format_hardware(detect_hardware()))
            return 0
        if parsed.command == "models":
            return _run_models(parsed.args)
        if parsed.command == "update":
            return _run_update(parsed.args)
        return _run_coordinator(parsed.args)
    return _run_runtime(args)


def legacy_main() -> int:
    """Compatibility entry point for installations created before the rename."""

    if sys.stderr.isatty():
        print("`asusai` is now `dairack`; continuing with Dairack.", file=sys.stderr)
    return main()
