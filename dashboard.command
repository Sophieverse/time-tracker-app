#!/bin/bash
# Double-click this in Finder to open the Time Tracker dashboard.
cd "$(dirname "$0")"
exec /usr/bin/python3 dashboard.py
