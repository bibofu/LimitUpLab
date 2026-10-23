"""Legacy ReAct runtime and replay support.

Production chat uses ``app.agents.runner``. This package remains for offline
Golden evaluation, quality replay and historical saved-run inspection; it is
not reachable from the public chat entry.
"""
