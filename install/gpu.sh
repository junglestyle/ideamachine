#!/usr/bin/env bash
# Dev box GPU worker: finding names said (policy C, decision 0008) as a kind on the dev box's corral worker. Each
# job takes one chunk of the segments not yet looked at, runs GLiNER on the GPU, keeps what it finds in the NAS's
# database (im.segment_names), and requeues itself until the backlog is done. The NAS never waits for it: anything
# not looked at yet is read on the NAS's CPU when a payload needs it. While the NAS has a rebuild database
# (install/rebuild.sh), a second kind does the same for it. Run as your normal user on the dev box, after
# install/nas.sh. Safe to re-run; also how updates are deployed.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

GPU_VENV="$HOME/.local/share/ideamachine/gpu-venv"
GPU_ENV="$HOME/.config/ideamachine/gpu.env"
GPU_REBUILD_ENV="$HOME/.config/ideamachine/gpu-rebuild.env"   # the same, for the rebuild database (decision 0012)
CORRAL_CONFIG="$HOME/.config/corral"

[ "$(id -u)" -ne 0 ] || die "run as your normal user, not root: the corral worker and its config are per user"

say "Checking the GPU"
nvidia-smi -L >/dev/null 2>&1 || die "nvidia-smi fails. If it reports a version mismatch, reboot, then re-run."
nvidia-smi -L

say "Checking ssh to the NAS works unattended"
# Corral's worker has no terminal and no ssh agent, so the key must work without either.
env -u SSH_AUTH_SOCK ssh -o BatchMode=yes nas true \
    || die "'ssh nas' needs a key that works without a passphrase prompt or agent. Fix ~/.ssh, then re-run."

say "The names role's connection, from the NAS (install/nas.sh writes it)"
url="$(ssh -o BatchMode=yes nas "cat '$NAS_ROOT/gpu/names.env'" | sed -n 's/^IM_DATABASE_URL=//p')" \
    && [ -n "$url" ] || die "can't read $NAS_ROOT/gpu/names.env over ssh: run install/nas.sh there, then re-run."
say "Checking the NAS lets ssh forward to Postgres on its loopback"
# TrueNAS turns forwarding off by default. -W opens one forwarded connection and fails at once if it's refused.
if ssh -o BatchMode=yes -W 127.0.0.1:55433 nas </dev/null 2>&1 >/dev/null | grep -q "administratively prohibited"; then
    die "the NAS's ssh server doesn't allow TCP forwarding. In TrueNAS: System > Services > SSH, tick Allow TCP
Port Forwarding, save, then re-run."
fi
mkdir -p "$(dirname "$GPU_ENV")"
(umask 077; printf 'IM_DATABASE_URL=%s\nIM_SSH_TUNNEL=nas\n' "$url" > "$GPU_ENV")
# While there's a rebuild database (install/rebuild.sh writes its connection), a second kind finds names for it.
rebuild_names="$NAS_ROOT/gpu/names-rebuild.env"
rebuild_url="$(ssh -o BatchMode=yes nas "[ ! -f '$rebuild_names' ] || cat '$rebuild_names'" \
    | sed -n 's/^IM_DATABASE_URL=//p')"
if [ -n "$rebuild_url" ]; then
    (umask 077; printf 'IM_DATABASE_URL=%s\nIM_SSH_TUNNEL=nas\n' "$rebuild_url" > "$GPU_REBUILD_ENV")
    echo "and the rebuild database's"
else
    rm -f "$GPU_REBUILD_ENV"
fi

say "Python 3.13 environment in $GPU_VENV"
command -v uv >/dev/null || die "install uv first: https://docs.astral.sh/uv/getting-started/installation/"
uv python install 3.13
[ -x "$GPU_VENV/bin/python" ] || uv venv --python 3.13 "$GPU_VENV"
# Without this project's sources, so torch comes from PyPI with CUDA rather than the NAS image's CPU-only index.
# A copy, not editable, reinstalled every run: the version doesn't change with the code.
VIRTUAL_ENV="$GPU_VENV" uv pip install --quiet --no-sources --reinstall-package ideamachine "$REPO_DIR[ner]"
(cd / && "$GPU_VENV/bin/python" -c "import im.names") || die "the GPU environment can't import im.names"
"$GPU_VENV/bin/python" -c "import torch; assert torch.cuda.is_available(), 'CUDA not available to torch'"

say "GLiNER, at the revision the NAS image has"
"$GPU_VENV/bin/python" -c "from im.names import Gliner; print(Gliner().per_text(['I told Julie Chen and Raj.']))"

say "Names kind on the corral worker (github.com/junglestyle/corral)"
systemctl --user is-active -q corral-worker && [ -f "$CORRAL_CONFIG/worker.toml" ] \
    || die "no corral worker running from $CORRAL_CONFIG. Set one up as a user unit (docs/running.md in
junglestyle/corral), then re-run."
grep -q '"gpu"' "$CORRAL_CONFIG/worker.toml" \
    || die "the corral worker in $CORRAL_CONFIG/worker.toml doesn't offer \"gpu\"; add it to offers, then re-run"
mkdir -p "$CORRAL_CONFIG/kinds.d"
# A shell only to load the connection; exec, so the worker's kill reaches Python (which closes its ssh tunnel).
if {
    cat <<EOF
# Written by ideamachine's install/gpu.sh on every run.
# Enqueue with: corral enqueue ideamachine.names --needs gpu --key ideamachine.names
[kinds."ideamachine.names"]
argv = ["/bin/sh", "-c", "set -a; . $GPU_ENV; exec $GPU_VENV/bin/im names job"]
timeout = "30m"
EOF
    [ -z "$rebuild_url" ] || cat <<EOF

# The rebuild database's: corral enqueue ideamachine.names-rebuild --needs gpu --key ideamachine.names-rebuild
[kinds."ideamachine.names-rebuild"]
argv = ["/bin/sh", "-c", "set -a; . $GPU_REBUILD_ENV; exec $GPU_VENV/bin/im names job"]
timeout = "30m"
EOF
} | write_if_changed "$CORRAL_CONFIG/kinds.d/ideamachine.toml"
then
    systemctl --user restart corral-worker
    echo "kinds updated, worker restarted"
fi
echo "kinds in $CORRAL_CONFIG/kinds.d/ideamachine.toml"

say "First chunk (here, not through corral, to see it work)"
(set -a; . "$GPU_ENV"; CORRAL_SUGGESTED_ITEMS=500 "$GPU_VENV/bin/im" names job) || die "the job failed (above)"

say "Done. For the backlog: corral enqueue ideamachine.names --needs gpu --key ideamachine.names (it requeues
itself until it's done)."
[ -z "$rebuild_url" ] || echo "For the rebuild database's: corral enqueue ideamachine.names-rebuild --needs gpu \
--key ideamachine.names-rebuild"
