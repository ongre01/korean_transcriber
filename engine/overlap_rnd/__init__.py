"""MM-DEV-018 R&D: recovering speech that single-channel selection loses.

V1 multi-input transcription keeps exactly one recording for every instant,
so a second, simultaneous talker or a short far-field remark heard better by
an unselected device can disappear from the transcript.  This package
compares the candidate remedies against V1 on controlled cases built from
real recordings:

* ``beamform_das``: delay-and-sum of the aligned recordings;
* ``separation_*``: a pretrained two-speaker separation model on the V1
  output, each separated stream transcribed and merged;
* ``merge_channels``: every recording transcribed and the transcripts merged.

The package is an experiment, not a product feature: nothing in the Qt GUI or
``backend_bridge.py`` imports it.
"""
