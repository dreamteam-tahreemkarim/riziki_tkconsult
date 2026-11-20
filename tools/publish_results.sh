#!/usr/bin/env bash
# Commit whatever a workflow wrote under dashboard/data and push it.
# Another job may have pushed first. index.json is rebuilt from the result
# files, so: pull, rebuild, push, and retry a few times if needed.
#   tools/publish_results.sh "scan N0040_E03600 2026"
set -u
msg="$1"
git config user.name "riziki-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
for attempt in 1 2 3 4 5; do
  git checkout -- dashboard/data/index.json 2>/dev/null || true
  git pull --rebase --autostash origin "${GITHUB_REF_NAME}"
  python -m mapping.index rebuild
  git add dashboard/data
  git diff --cached --quiet && exit 0
  git commit -m "$msg" --quiet
  git push origin "HEAD:${GITHUB_REF_NAME}" && exit 0
  git reset --soft HEAD~1
  sleep $((attempt * 7))
done
exit 1
