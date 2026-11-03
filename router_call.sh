#!/bin/bash

cd /home/ubuntu/ai-router || exit 1
source .venv/bin/activate
exec python router_call.py "$@"
