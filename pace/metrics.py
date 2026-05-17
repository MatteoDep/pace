"""Curated per-metric thresholds for color cues."""

from pace.render.colors import Threshold

BODY_BATTERY = Threshold(warn=25, good=60, higher_better=True)
TRAINING_READINESS = Threshold(warn=25, good=75, higher_better=True)
SLEEP_SCORE = Threshold(warn=60, good=80, higher_better=True)
STRESS_AVG = Threshold(warn=50, good=25, higher_better=False)
HRV_NIGHT_AVG = Threshold(warn=40, good=60, higher_better=True)
RESTING_HR = Threshold(warn=70, good=55, higher_better=False)
STEPS = Threshold(warn=5000, good=10000, higher_better=True)
