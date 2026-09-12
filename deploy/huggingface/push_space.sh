#!/usr/bin/env bash
# Publish the app to a Hugging Face Space.
#
#   HF_TOKEN=hf_xxx deploy/huggingface/push_space.sh [space-name]
#
# The Space is its own git repository, separate from GitHub. Rather than adding
# a second remote to the project repo and having to keep a Space-specific
# README on a branch, this assembles the Space contents in a temporary clone:
# the application files, plus deploy/huggingface/README.md, whose YAML
# frontmatter is what tells Hugging Face to build the Dockerfile and which port
# to route to.
#
# data/ is not copied. The corpus is generated during the Docker build from the
# seed in the code, exactly as it is locally.

set -euo pipefail

SPACE_NAME="${1:-reunifyai}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HF="$ROOT/.venv/bin/hf"

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "HF_TOKEN is not set. Create one with write access at" >&2
  echo "  https://huggingface.co/settings/tokens" >&2
  exit 1
fi

USERNAME="$("$HF" auth whoami --token "$HF_TOKEN" | head -1 | tr -d '[:space:]')"
REPO="$USERNAME/$SPACE_NAME"
echo "Publishing to Space: $REPO"

"$HF" repo create "$REPO" --repo-type space --space_sdk docker \
      --token "$HF_TOKEN" --exist-ok >/dev/null

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git clone -q "https://oauth2:$HF_TOKEN@huggingface.co/spaces/$REPO" "$WORK/space"
cd "$WORK/space"

# Replace the Space's contents with the current application.
git rm -rq --ignore-unmatch . 2>/dev/null || true
mkdir -p engine web scripts
cp -R "$ROOT/engine/." engine/
cp -R "$ROOT/web/." web/
cp -R "$ROOT/scripts/." scripts/
cp "$ROOT/Dockerfile" "$ROOT/requirements.txt" .
cp "$ROOT/deploy/huggingface/README.md" README.md
find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

git add -A
if git diff --cached --quiet; then
  echo "Space is already up to date."
else
  git -c user.email="noreply@huggingface.co" -c user.name="ReunifyAI deploy" \
      commit -qm "Deploy ReunifyAI: synthetic-data candidate matching for human review"
  git push -q origin main
  echo "Pushed."
fi

echo
echo "Space:  https://huggingface.co/spaces/$REPO"
echo "App:    https://$(echo "$USERNAME" | tr '[:upper:]' '[:lower:]')-$(echo "$SPACE_NAME" | tr '[:upper:]' '[:lower:]').hf.space"
echo "Demo:   .../?demo=1"
echo
echo "The first build takes a few minutes: it installs dependencies and draws"
echo "the 160 synthetic faces. Watch it under the Space's Logs tab."
