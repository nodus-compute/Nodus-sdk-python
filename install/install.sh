#!/bin/sh
# The complete function is parsed before any installation begins.
nodus_install() (
    set -eu
    umask 077
    nodus_tmp=$(mktemp -d)
    trap 'rm -rf "$nodus_tmp"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    nodus_version='@@SDK_VERSION@@'
    nodus_runtime="$HOME/.nodus/agent-tools/$nodus_version-1"
    for nodus_dir in "$HOME/.nodus" "$HOME/.nodus/agent-tools" "$HOME/.nodus/python" "$nodus_runtime"; do
        if [ -L "$nodus_dir" ]; then
            printf '%s\n' 'A Nodus installation folder is a symbolic link. Use manual setup.' >&2
            exit 1
        fi
    done
    case "$(uname -s)-$(uname -m)" in
        Darwin-arm64) nodus_target=aarch64-apple-darwin; nodus_hash=85f00cbdc6dd3e97eba4c31b4d014375a9fdfe8f570023b84e5102fc3456896b ;;
        Darwin-x86_64) nodus_target=x86_64-apple-darwin; nodus_hash=8dcf05a8c809bb3c471d2b614788ba27a6e41298fc8c31ac84b5f4339fd468e5 ;;
        Linux-aarch64|Linux-arm64) nodus_target=aarch64-unknown-linux-gnu; nodus_hash=d636d1b678e9e7f367ecb22b46bd1cabbed234d6bc3b4d96365d2b507f72f86c ;;
        Linux-x86_64) nodus_target=x86_64-unknown-linux-gnu; nodus_hash=fa82fd8dde8e8eefdecada6aa0889666556cfceb690d06e0c3bca49eb3070a63 ;;
        *) printf '%s\n' 'This installer supports macOS and glibc Linux on Intel and ARM64. See https://nodus-compute.ai/connect/ for other systems.' >&2; exit 1 ;;
    esac
    printf '%s\n' 'Preparing Nodus setup. No administrator access needed.'
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 2 \
        "https://github.com/astral-sh/uv/releases/download/0.12.17/uv-$nodus_target.tar.gz" -o "$nodus_tmp/uv.tar.gz"
    if command -v sha256sum >/dev/null 2>&1; then
        nodus_actual=$(sha256sum "$nodus_tmp/uv.tar.gz" | cut -d ' ' -f 1)
    else
        nodus_actual=$(shasum -a 256 "$nodus_tmp/uv.tar.gz" | cut -d ' ' -f 1)
    fi
    if [ "$nodus_actual" != "$nodus_hash" ]; then
        printf '%s\n' 'The runtime download failed its integrity check. Retry setup.' >&2
        exit 1
    fi
    tar -xzf "$nodus_tmp/uv.tar.gz" -C "$nodus_tmp"
    nodus_uv="$nodus_tmp/uv-$nodus_target/uv"
    mkdir -p "$HOME/.nodus/agent-tools"
    nodus_lock="$HOME/.nodus/agent-tools/.install-lock"
    if ! mkdir "$nodus_lock" 2>/dev/null; then
        printf '%s\n' 'Another Nodus setup may be running. Finish it before retrying. If it was interrupted, remove ~/.nodus/agent-tools/.install-lock.' >&2
        exit 1
    fi
    trap 'rmdir "$nodus_lock"; rm -rf "$nodus_tmp"' EXIT
    if [ ! -x "$nodus_runtime/bin/python" ]; then
        env -i HOME="$HOME" PATH="$PATH" UV_PYTHON_INSTALL_DIR="$HOME/.nodus/python" \
            "$nodus_uv" --no-config venv --managed-python --python 3.12 "$nodus_runtime"
    fi
    env -i HOME="$HOME" PATH="$PATH" "$nodus_uv" --no-config pip install \
        --python "$nodus_runtime/bin/python" --index-url https://pypi.org/simple \
        'nodus-compute[mcp]==@@SDK_VERSION@@' 'tomlkit==0.13.3' 'json5==0.12.1'
    cat > "$nodus_tmp/connect.py" <<'NODUS_CONNECT_PY'
@@CONNECT_SOURCE@@
NODUS_CONNECT_PY
    if [ -t 0 ]; then
        "$nodus_runtime/bin/python" -I "$nodus_tmp/connect.py" "$@"
    elif [ -r /dev/tty ] && (exec </dev/tty) 2>/dev/null; then
        "$nodus_runtime/bin/python" -I "$nodus_tmp/connect.py" "$@" </dev/tty
    else
        "$nodus_runtime/bin/python" -I "$nodus_tmp/connect.py" "$@"
    fi
)
nodus_install "$@"
