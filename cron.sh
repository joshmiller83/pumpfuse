#!/bin/bash
# Wrapper for cron — sets PATH and SSH agent so git push and ssh work headlessly.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="/Users/jomiller"
export SSH_AUTH_SOCK=$(ls /private/tmp/com.apple.launchd.*/Listeners 2>/dev/null | head -1)
cd "$(dirname "$0")"
python3 poll.py >> data/poll.log 2>&1
