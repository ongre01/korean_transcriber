"""MM-DEV-017 transcription-quality evaluation of multi-microphone input.

The package compares single-file transcription of every recording with the
multi-input (channel selection) transcription of the same meeting.  Every
condition runs through ``backend_bridge.py`` exactly as the Qt GUI starts it,
with one shared set of Whisper/diarization settings, so the comparison isolates
the input condition.  Accuracy metrics need a human reference transcript; the
reference-free measurements (time, channel switches, transcript disagreement)
are reported separately and are never presented as accuracy.
"""
