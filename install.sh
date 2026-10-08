#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
python_bin=""

is_python_ready() {
    "$1" -I -c 'import platform,sys; raise SystemExit(0 if platform.python_implementation() == "CPython" and sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

if [ -n "${BAIYI_PYTHON:-}" ]; then
    if [ ! -x "$BAIYI_PYTHON" ] || ! is_python_ready "$BAIYI_PYTHON"; then
        printf '%s\n' 'NOT_READY: BAIYI_PYTHON must name a runnable CPython 3.9+ interpreter.' >&2
        exit 1
    fi
    python_bin=$BAIYI_PYTHON
else
    # Prefer an existing package/user installation before the macOS developer shim.
    for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            resolved=$(command -v "$candidate")
            if [ "$resolved" = /usr/bin/python3 ] && [ "$(uname -s)" = Darwin ]; then
                # Avoid triggering a developer-tools installation dialog.
                if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
                    continue
                fi
            fi
            if is_python_ready "$resolved"; then
                python_bin=$resolved
                break
            fi
        fi
    done
fi

if [ -z "$python_bin" ]; then
    printf '%s\n' 'NOT_READY: CPython 3.9+ was not found. Install a supported Python from https://www.python.org/downloads/macos/ and rerun this script, or set BAIYI_PYTHON to an existing interpreter. Nothing was installed.' >&2
    exit 1
fi

exec "$python_bin" -I "$script_dir/install.py" "$@"
