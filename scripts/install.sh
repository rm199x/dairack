#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-}

if command -v uv >/dev/null 2>&1; then
    echo "Installing Dairack with uv tool..."
    uv tool install --force "$ROOT"
elif command -v pipx >/dev/null 2>&1; then
    echo "Installing Dairack with pipx..."
    pipx install --force "$ROOT"
else
    DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
    BIN_DIR=${DAIRACK_BIN_DIR:-${ASUSAI_BIN_DIR:-"$HOME/.local/bin"}}
    VENV=${DAIRACK_VENV:-${ASUSAI_VENV:-"$DATA_HOME/dairack/runtime-venv"}}
    if [ -z "$PYTHON" ] \
        && [ -x "$VENV/bin/python" ] \
        && "$VENV/bin/python" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1 \
        && "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
        echo "Updating Dairack in $VENV..."
    else
        if [ -z "$PYTHON" ]; then
            for candidate in python3 python3.13 python3.12 python3.11; do
                if command -v "$candidate" >/dev/null 2>&1 \
                    && "$candidate" -c 'import ensurepip, sys, venv; raise SystemExit(sys.version_info < (3, 11))' \
                        >/dev/null 2>&1; then
                    PYTHON=$(command -v "$candidate")
                    break
                fi
            done
        fi
        PYTHON=${PYTHON:-python3}
        if ! "$PYTHON" -c 'import ensurepip, sys, venv; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
            printf '%s\n' \
                "$PYTHON cannot create a Python 3.11+ virtual environment with pip." \
                "Set PYTHON to a suitable interpreter, install venv support" \
                "(for example python3-venv on Debian), or install uv/pipx." >&2
            exit 1
        fi
        echo "Installing Dairack into $VENV..."
        if ! "$PYTHON" -m venv "$VENV"; then
            printf '%s\n' \
                "Could not create a Python virtual environment." \
                "Install venv support for $PYTHON (for example python3-venv on Debian)," \
                "or install uv/pipx, then run this script again." >&2
            exit 1
        fi
    fi
    "$VENV/bin/python" -m pip install --upgrade "$ROOT"
    mkdir -p "$BIN_DIR"
    ln -sf "$VENV/bin/dairack" "$BIN_DIR/dairack"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) echo "Add $BIN_DIR to PATH to invoke dairack directly." ;;
    esac
fi

printf '\nInstalled. Next run:\n  dairack setup\n  dairack\n\nOptional diagnostics:\n  dairack doctor\n'
