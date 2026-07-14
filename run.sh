#!/bin/bash
# 30A Music Intelligence — serve the generated dashboard locally
# (docs/index.html is produced by the pipeline: python3 run_monitor.py)
cd "$(dirname "$0")/docs" && echo "Dashboard: http://localhost:8080" && python3 -m http.server 8080
