#!/usr/bin/env bash
set -e
ssh morse 'cd ~/morse-listener && git pull && ~/.local/bin/uv sync && sudo systemctl restart morse'
