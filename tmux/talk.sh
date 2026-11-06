#!/usr/bin/env bash
# Builds the whole stage in one tmux session. Re-running attaches to the existing one.
#
#   0 slides   presenterm
#   1 stage    server log | dashboard
#              ctl shell  |
#   2 pipes    v1 | v2 | v3 collectors (commands typed, NOT started: hit Enter live)
#   3 lake     duckdb | nvim on warehouse/
#
# NO_ATTACH=1 just talk   pre-builds the session (e.g. before you walk on stage).
# Jump with prefix+0..3, or the F1..F4 bindings below (no prefix).
set -euo pipefail
S=apitalk
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if tmux has-session -t $S 2>/dev/null; then
  exec tmux attach -t $S
fi

[ -f data/source.db ] || just seed

COLS=$(tput cols 2>/dev/null || true); LINES_=$(tput lines 2>/dev/null || true)
tmux new-session -d -s $S -n slides -c "$ROOT" -x "${COLS:-200}" -y "${LINES_:-50}"
tmux source-file "$ROOT/tmux/talk.conf"
tmux send-keys -t $S:slides "just slides" C-m

tmux new-window -t $S -n stage -c "$ROOT"
tmux send-keys -t $S:stage "just server" C-m
tmux split-window -t $S:stage -h -l 62% -c "$ROOT"
tmux send-keys -t $S:stage.1 "sleep 2 && just dash" C-m
tmux split-window -t $S:stage.0 -v -l 35% -c "$ROOT"
tmux send-keys -t $S:stage.1 "just ctl" ""

tmux new-window -t $S -n pipes -c "$ROOT"
tmux send-keys -t $S:pipes "just collect v1" ""
tmux split-window -t $S:pipes -h -c "$ROOT"
tmux send-keys -t $S:pipes.1 "just collect v2" ""
tmux split-window -t $S:pipes.1 -h -c "$ROOT"
tmux send-keys -t $S:pipes.2 "just collect v3" ""
tmux select-layout -t $S:pipes even-horizontal

tmux new-window -t $S -n lake -c "$ROOT"
tmux send-keys -t $S:lake "just wh" ""
tmux split-window -t $S:lake -h -l 50% -c "$ROOT"
tmux send-keys -t $S:lake.1 "nvim warehouse/" C-m

tmux select-window -t $S:slides
[ -n "${NO_ATTACH:-}" ] && { echo "session $S ready: tmux attach -t $S"; exit 0; }
exec tmux attach -t $S
