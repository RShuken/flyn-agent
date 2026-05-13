#!/usr/bin/env bash
# Transcribe a local video file using whisper + ffmpeg.
# Output: <basename>.transcript.md alongside the source file.
#
# Usage:
#   ./video_transcribe.sh <path-to-video> [project-slug] [meeting-folder-slug]
#
# Example:
#   ./video_transcribe.sh /tmp/lesson-sharing.mp4 openliteracy 2026-05-11_pearl-platform-video

set -euo pipefail

VIDEO="${1:-}"
PROJECT="${2:-}"
MEETING_SLUG="${3:-}"

[ -z "$VIDEO" ] && { echo "usage: $0 <video> [project-slug] [meeting-slug]"; exit 1; }
[ ! -f "$VIDEO" ] && { echo "no such file: $VIDEO"; exit 1; }

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
BASE=$(basename "$VIDEO" | sed 's/\.[^.]*$//')

echo "→ extracting audio from $VIDEO"
ffmpeg -y -nostats -loglevel error -i "$VIDEO" -vn -acodec pcm_s16le -ar 16000 -ac 1 "$WORK_DIR/$BASE.wav"

echo "→ running whisper (small model, ~1 min per audio-min) "
whisper "$WORK_DIR/$BASE.wav" \
    --model small \
    --language en \
    --output_format txt \
    --output_format vtt \
    --output_dir "$WORK_DIR" \
    --task transcribe \
    --verbose False

# Build a clean markdown
OUT_DIR=$(dirname "$VIDEO")
OUT_FILE="$OUT_DIR/$BASE.transcript.md"
{
    echo "# Transcript: $BASE"
    echo
    echo "- **Source:** \`$VIDEO\`"
    echo "- **Transcribed:** $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "- **Model:** whisper-small (en)"
    [ -n "$PROJECT" ] && echo "- **Project:** $PROJECT"
    [ -n "$MEETING_SLUG" ] && echo "- **Meeting:** $MEETING_SLUG"
    echo
    echo "---"
    echo
    echo "## Plain transcript"
    echo
    cat "$WORK_DIR/$BASE.txt"
    echo
    echo "## Timed (VTT)"
    echo
    echo "\`\`\`"
    cat "$WORK_DIR/$BASE.vtt"
    echo "\`\`\`"
} > "$OUT_FILE"

echo "→ wrote $OUT_FILE"

# If a project + meeting slug were specified AND the project has a known repo,
# also route a copy into docs/00-source/meetings/<slug>/Transcript_&_Recording/
if [ -n "$PROJECT" ] && [ -n "$MEETING_SLUG" ]; then
    REPO_PATH=$(python3 -c "
import yaml
try:
    d = yaml.safe_load(open('$HOME/.openclaw/projects/$PROJECT/config.yaml'))
    print(d['repo']['path'])
except Exception:
    pass
")
    if [ -n "$REPO_PATH" ] && [ -d "$REPO_PATH" ]; then
        DEST_DIR="$REPO_PATH/docs/00-source/meetings/$MEETING_SLUG/Transcript_&_Recording"
        mkdir -p "$DEST_DIR"
        cp "$OUT_FILE" "$DEST_DIR/"
        echo "→ also copied to $DEST_DIR/"
    fi
fi
