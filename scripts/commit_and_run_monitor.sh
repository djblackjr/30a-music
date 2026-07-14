#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="/Users/dannyblack/Documents/30a-music.worktrees/agents-git-status-check-and-commit-images"
cd "$REPO_DIR"
# Enable nullglob for filename patterns
shopt -s nullglob
# Collect image files in inbox
files=(images/inbox/*.{png,jpg,jpeg,gif,PNG,JPG,JPEG,GIF})
if [ ${#files[@]} -gt 0 ]; then
  echo "Found ${#files[@]} inbox image(s). Running monitor."
else
  echo "No inbox images found. Running monitor."
fi

/usr/bin/env python3 run_monitor.py

paths=(images/inbox images/processed images/failed docs/index.html data/events.db)

git add -f -A -- "${paths[@]}"
if ! git diff --cached --quiet -- "${paths[@]}"; then
  basenames=()
  if [ ${#files[@]} -gt 0 ]; then
    for f in "${files[@]}"; do basenames+=("$(basename "$f")"); done
    msg_files="$(printf ", %s" "${basenames[@]}")"
    msg_files="${msg_files#, }"
    commit_msg="Process inbox images and update reports: ${msg_files}"
  else
    commit_msg="Update dashboard and event data"
  fi
  git commit -m "$commit_msg" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" || true
  git push origin HEAD || true
fi
