#!/usr/bin/env bash
# Commit result files back to main from a workflow. Usage: ops/commit_back.sh "<message>" <paths...>
# Tries GITHUB_TOKEN first, then GH_PAT if present. Errors surface as job annotations (readable via the API).
set -u
MSG="$1"; shift
git config user.name hn-bot
git config user.email bot@meteor.city
if ! ADD=$(git add -A -- "$@" 2>&1); then echo "::warning::git add: $ADD"; fi
if git diff --cached --quiet; then echo "nothing to commit"; exit 0; fi
if ! OUT=$(git commit -qm "$MSG" 2>&1); then echo "::error::git commit: $OUT"; exit 1; fi
for i in 1 2 3; do
  if ! OUT=$(git pull --rebase origin main 2>&1); then echo "::warning::git pull --rebase (try $i): $OUT"; fi
  if OUT=$(git push origin HEAD:main 2>&1); then echo "pushed: $OUT"; exit 0; else echo "::warning::push with GITHUB_TOKEN (try $i): $OUT"; fi
  if [ -n "${GH_PAT:-}" ]; then
    if OUT=$(git push "https://x-access-token:${GH_PAT}@github.com/${GITHUB_REPOSITORY}.git" HEAD:main 2>&1); then echo "pushed with PAT"; exit 0; else echo "::warning::push with PAT (try $i): ${OUT//${GH_PAT}/***}"; fi
  fi
  sleep 5
done
echo "::error::could not push the results"
exit 1
