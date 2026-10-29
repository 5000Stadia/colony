"""Bounded transcript rows: an oversized tool result must not allocate the whole line."""


def read_line(handle, limit=300_000, *, edges=0):
    """Return (bytes or None when oversized, complete). Empty bytes means EOF.

    Leave the handle after the row, even when discarding it. Callers retain the
    previous complete offset when a row is still being written.

    With `edges`, an oversized row returns only that many bytes from its start
    and end, separated by a newline. This is not JSON: activity readers use its
    metadata without retaining a tool result's body. The default still discards it.
    """
    line = handle.readline(limit + 1)
    if len(line) <= limit:
        return line, line.endswith(b'\n')
    prefix, tail = line[:edges], line[-edges:] if edges else b''
    while line and not line.endswith(b'\n'):
        line = handle.readline(limit + 1)
        if edges and line:
            tail = (tail + line)[-edges:]
    return prefix + b'\n' + tail if edges else None, line.endswith(b'\n')
