#!/usr/bin/env bash
# Publish the app to a Hugging Face STATIC Space.
#
# Docker Spaces now require a PRO subscription on free CPU hardware; static
# Spaces remain free for everyone. The matcher is deterministic over a fixed
# corpus, so scripts/build_static.py precomputes every answer the API could
# give and the interface runs with no server at all.
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
PY="$ROOT/.venv/bin/python"

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "HF_TOKEN is not set. Create one with write access at" >&2
  echo "  https://huggingface.co/settings/tokens" >&2
  exit 1
fi

# The hf CLI's flags have moved between versions; the Python API has not.
USERNAME="$("$PY" - <<'PY'
import os
from huggingface_hub import HfApi
print(HfApi(token=os.environ["HF_TOKEN"]).whoami()["name"])
PY
)"
REPO="$USERNAME/$SPACE_NAME"
echo "Publishing to Space: $REPO"

SPACE_NAME="$SPACE_NAME" REPO="$REPO" "$PY" - <<'PY'
import os
from huggingface_hub import HfApi
HfApi(token=os.environ["HF_TOKEN"]).create_repo(
    repo_id=os.environ["REPO"], repo_type="space",
    space_sdk="static", exist_ok=True)
PY

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Building static site..."
(cd "$ROOT" && PYTHONPATH="$ROOT" "$PY" scripts/build_static.py --out "$WORK/dist")
cp "$ROOT/deploy/huggingface/README.md" "$WORK/dist/README.md"

# Uploaded through the Hub API rather than git. The corpus includes 160 PNGs,
# and the Hub's pre-receive hook requires binaries to arrive via git-lfs, which
# would mean installing and configuring it locally. upload_folder handles that
# negotiation itself, and it is the supported path for exactly this case.
REPO="$REPO" SRC="$WORK/dist" "$PY" - <<'PY'
import os
from huggingface_hub import HfApi
api = HfApi(token=os.environ["HF_TOKEN"])
api.upload_folder(
    repo_id=os.environ["REPO"],
    repo_type="space",
    folder_path=os.environ["SRC"],
    commit_message="Deploy ReunifyAI: synthetic-data candidate matching for human review",
    delete_patterns="*",
)
print("Uploaded.")
PY

echo
echo "Space:  https://huggingface.co/spaces/$REPO"
# Static Spaces are served from *.static.hf.space. The plain *.hf.space
# address 404s, which looks exactly like a failed deploy.
echo "App:    https://$(echo "$USERNAME" | tr '[:upper:]' '[:lower:]')-$(echo "$SPACE_NAME" | tr '[:upper:]' '[:lower:]').static.hf.space"
echo "Demo:   .../?demo=1"
echo
echo "A static Space goes live as soon as the push finishes -- there is no"
echo "build step, no cold start, and nothing to keep awake."
