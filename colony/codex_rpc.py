"""PROVIDER: bounded local Codex RPC over its Unix WebSocket or test stdio.

No turns are submitted or retried here. A lost response leaves its outcome unknown.
"""
import json
import base64
import os
import select
import socket as sockets
import struct
import subprocess
import time
from pathlib import Path


class RPCError(RuntimeError):
    pass


class Client:
    def __init__(self, socket=None, *, command=None, env=None, cwd=None, timeout=15):
        self.timeout, self.sequence, self.buffer = timeout, 0, b""
        self.socket, self.process = None, None
        try:
            if command:
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL, env=env, cwd=cwd)
            else:
                from .console import accept, read_exact
                self.socket = sockets.socket(sockets.AF_UNIX, sockets.SOCK_STREAM)
                self.socket.settimeout(timeout)
                # Codex publishes a rendezvous symlink to a short protected path.
                self.socket.connect(str(Path(socket).resolve()))
                key = base64.b64encode(os.urandom(16)).decode()
                self.socket.sendall(('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                                     'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                                     f'Sec-WebSocket-Key: {key}\r\n\r\n').encode())
                headers = b''
                while not headers.endswith(b'\r\n\r\n'):
                    if len(headers) > 16384:
                        raise RPCError('Invalid Codex WebSocket handshake')
                    headers += read_exact(self.socket, 1)
                lines = headers.decode().split('\r\n')
                fields = {k.lower(): v for line in lines[1:] if ':' in line for k, v in [line.split(':', 1)]}
                if ' 101 ' not in lines[0] or fields.get('sec-websocket-accept', '').strip() != accept(key):
                    raise RPCError('Codex WebSocket handshake failed')
            self.call("initialize", {"clientInfo": {"name": "colony", "version": "1"},
                                     "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized"})
        except BaseException:
            self.close()
            raise

    def send(self, value):
        if self.socket:
            self.frame(json.dumps(value).encode())
            return
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        self.process.stdin.flush()

    def frame(self, data, opcode=1):
        size, mask = len(data), os.urandom(4)
        length = bytes([size | 128]) if size < 126 else (bytes([126 | 128]) + struct.pack('>H', size)
                    if size < 65536 else bytes([127 | 128]) + struct.pack('>Q', size))
        self.socket.sendall(bytes([128 | opcode]) + length + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(data)))

    def websocket(self, deadline):
        from .console import read_exact
        data = bytearray()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RPCError('Codex did not answer; the operation was not retried')
            self.socket.settimeout(left)
            first, second = read_exact(self.socket, 2)
            opcode, size = first & 15, second & 127
            if size == 126:
                size = struct.unpack('>H', read_exact(self.socket, 2))[0]
            elif size == 127:
                size = struct.unpack('>Q', read_exact(self.socket, 8))[0]
            if size + len(data) > 32 * 1024 * 1024 or second & 128 or first & 112:
                raise RPCError('Unsupported Codex WebSocket frame')
            part = read_exact(self.socket, size)
            if opcode == 8:
                raise RPCError('Codex connection closed; the operation was not retried')
            if opcode == 9:
                self.frame(part, 10)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1):
                raise RPCError('Unexpected Codex WebSocket message')
            data.extend(part)
            if first & 128:
                return json.loads(data)

    def receive(self, deadline):
        if self.socket:
            return self.websocket(deadline)
        while b"\n" not in self.buffer:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.process.stdout], [], [], left)[0]:
                raise RPCError("Codex did not answer; the operation was not retried")
            data = os.read(self.process.stdout.fileno(), 65536)
            if not data:
                raise RPCError("Codex connection closed; the operation was not retried")
            self.buffer += data
            if len(self.buffer) > 32 * 1024 * 1024:
                raise RPCError("Codex response exceeds the local RPC limit")
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def call(self, method, params=None):
        self.sequence += 1
        request = self.sequence
        self.send({"id": request, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        while True:
            value = self.receive(deadline)
            if value.get("id") != request or "method" in value:
                continue                       # never answer an app/TUI approval or submit input
            if "error" in value:
                raise RPCError(value["error"].get("message", "Codex rejected the request"))
            return value["result"]

    def close(self):
        if self.socket:
            self.socket.close()
            self.socket = None
        if not self.process:
            return
        if self.process.poll() is None:
            self.process.terminate()             # only our transport proxy, never the daemon
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
