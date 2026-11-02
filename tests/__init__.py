import os

# The suite runs from inside a colony console as often as not: what marks that console's project and session must not
# reach a test's colony, or its commands act as the real project, wherever the test runs them (board.root_of).
for _marker in ("COLONY_PROJECT", "COLONY_CONSOLE"):
    os.environ.pop(_marker, None)

# The person's own colony home (their projects list, settings, every project's state) is never a test's to touch. Named
# here, before any test sets a home of its own, board.home() refuses it for the rest of the run: a test that leaves
# COLONY_BOARD_HOME unset, or a thread still running after its test's home was put back, fails instead of writing there.
if "COLONY_TEST_FORBIDS" not in os.environ:
    from colony import board as _board
    os.environ["COLONY_TEST_FORBIDS"] = str(_board.home())


def patience(seconds):
    """A wait a test allows real programs and threads, stretched while the machine is busy (up to four times): the
    suite shares the machine with the live board, its consoles, helpers' test runs and the voice, which takes most
    cores while it reads aloud, and a window that holds on an idle machine ran out on one loaded past its cores."""
    try:
        load = os.getloadavg()[0] / (os.cpu_count() or 1)
    except OSError:
        load = 1.0
    return seconds * min(4.0, max(1.0, load))
