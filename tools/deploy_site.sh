#!/usr/bin/env bash
# Build the locked, view-only copy of the dashboard and push it to a folder of the
# website's repo. Used by .github/workflows/deploy_site.yml; runs locally too.
#   RIZIKI_PASSWORD=... tools/deploy_site.sh WEBSITE_CLONE SITE_DIR "commit message"
# WEBSITE_CLONE is a clone of the website repo that can push. Only SITE_DIR in it changes.
set -u
repo=$(cd "$(dirname "$0")/.." && pwd)
site="$1"; dir="$2"; msg=$(printf '%s' "${3:-Riziki map update}" | head -n 1)
case "$dir" in
  ""|"."|"./"|"/"|/*|*..*) echo "SITE_DIR must be a sub-folder of the website, such as riziki (got '$dir')."; exit 1 ;;
esac
cd "$site" || { echo "No website clone at $site"; exit 1; }
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "The website clone has uncommitted changes; commit or stash them first."; exit 1
fi
branch=$(git rev-parse --abbrev-ref HEAD)
git config user.name "riziki-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
for attempt in 1 2 3 4 5; do
  git pull --rebase --quiet origin "$branch" || { git rebase --abort 2>/dev/null; sleep $((attempt * 5)); continue; }
  python "$repo/tools/build_site.py" "$dir" || exit 1
  git add -A -- "$dir"
  if git diff --cached --quiet; then echo "Website already up to date."; exit 0; fi
  git commit -m "$msg" --quiet
  git push --quiet origin "HEAD:$branch" && { echo "Pushed to $branch: $dir"; exit 0; }
  git reset --hard --quiet HEAD~1               # someone pushed first: start again from theirs
  sleep $((attempt * 5))
done
echo "Couldn't push after 5 tries."; exit 1
