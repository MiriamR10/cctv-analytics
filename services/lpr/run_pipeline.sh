#!/bin/sh
# Full LPR pipeline: detect + OCR -> interpolate missing frames -> render annotated video
# -> publish plates to the central forensic service (when FORENSIC_URL is set)
set -e

python main.py
python add_missing_data.py
python visualize.py

if [ -n "$FORENSIC_URL" ]; then
    python publish_events.py
fi
