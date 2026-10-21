#!/bin/sh
# Full LPR pipeline: detect + OCR -> interpolate missing frames -> render annotated video
set -e

python main.py
python add_missing_data.py
python visualize.py
