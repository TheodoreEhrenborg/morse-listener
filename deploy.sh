#!/usr/bin/env bash
set -e
ssh morse 'cd ~/morse-listener && git fetch origin && git reset --hard origin/main && ~/.local/bin/uv sync && sudo systemctl restart morse'
