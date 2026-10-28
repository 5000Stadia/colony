import os

# The suite runs from inside a colony console as often as not: what marks that console's project and session must not
# reach a test's colony, or its commands act as the real project, wherever the test runs them (board.root_of).
for _marker in ("COLONY_PROJECT", "COLONY_CONSOLE"):
    os.environ.pop(_marker, None)
