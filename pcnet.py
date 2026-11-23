"""PC (Python 2.7) implementation of localsend22's async "net" interface.

All callbacks run on the single loop thread started with run().
"""
import errno
import heapq
import select
import socket
import threading
import time

_AGAIN = (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINPROGRESS, errno.EALREADY)


class Conn(object):
    def __init__(self, net, sock):
        self.net = net
        self.sock = sock
        self.sock.setblocking(0)
        self.on_data = None
        self.out = ""
        self.on_done = None
        self.on_err = None
        self.closed = False
        net._conns[self] = 1

    def recv(self, on_data):
        if self.closed:
            self.net.later(0, lambda: on_data(""))
            return
        self.on_data = on_data

    def send(self, data, on_done, on_err):
        if self.closed:
            self.net.later(0, lambda: on_err("closed"))
            return
        self.out = data
        self.on_done = on_done
        self.on_err = on_err
        if not data:
            self.out = ""
            cb, self.on_done, self.on_err = self.on_done, None, None
            self.net.later(0, cb)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.net._conns.pop(self, None)
        try:
            self.sock.close()
        except Exception:
            pass

    def local_ip(self):
        try:
            return self.sock.getsockname()[0]
        except Exception:
            return None

    # loop internals
    def _want_read(self):
        return self.on_data is not None

    def _want_write(self):
        return self.on_done is not None

    def _do_read(self):
        try:
            data = self.sock.recv(16384)
        except socket.error as e:
            if e.args[0] in _AGAIN:
                return
            data = ""
        cb, self.on_data = self.on_data, None
        cb(data)

    def _do_write(self):
        try:
            n = self.sock.send(self.out)
        except socket.error as e:
            if e.args[0] in _AGAIN:
                return
            cb, self.on_done, self.on_err = self.on_err, None, None
            self.out = ""
            cb(str(e))
            return
        self.out = self.out[n:]
        if not self.out:
            cb, self.on_done, self.on_err = self.on_done, None, None
            cb()


class SelectNet(object):
    def __init__(self):
        self._conns = {}
        self._listeners = {}   # sock -> on_conn
        self._udp = {}         # sock -> on_packet
        self._connecting = {}  # sock -> (on_ok, on_err)
        self._timers = []
        self._seq = 0
        self._queue = []
        self._qlock = threading.Lock()
        self._running = False

    # ---- interface used by localsend22
    def listen(self, port, on_conn):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", port))
        s.listen(16)
        s.setblocking(0)
        self._listeners[s] = on_conn
        return s

    def unlisten(self, s):
        self._listeners.pop(s, None)
        try:
            s.close()
        except Exception:
            pass

    def connect(self, host, port, on_ok, on_err):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setblocking(0)
        try:
            r = s.connect_ex((host, port))
        except socket.error as e:
            self.later(0, lambda: on_err(str(e)))
            return
        if r not in (0,) + _AGAIN:
            s.close()
            self.later(0, lambda: on_err("connect error %d" % r))
            return
        self._connecting[s] = (on_ok, on_err)

    def later(self, seconds, func):
        self._seq += 1
        heapq.heappush(self._timers, (time.time() + seconds, self._seq, func))

    def udp_send(self, host, port, data):
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            u.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            u.sendto(data, (host, port))
        finally:
            u.close()

    def udp_listen(self, port, on_packet):
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        u.bind(("", port))
        u.setblocking(0)
        self._udp[u] = on_packet
        return u

    # ---- test helpers
    def call(self, func):
        self._qlock.acquire()
        try:
            self._queue.append(func)
        finally:
            self._qlock.release()

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            self._qlock.acquire()
            q, self._queue = self._queue, []
            self._qlock.release()
            for f in q:
                self._safe(f)
            now = time.time()
            while self._timers and self._timers[0][0] <= now:
                f = heapq.heappop(self._timers)[2]
                self._safe(f)
            timeout = 0.05
            if self._timers:
                timeout = max(0, min(timeout, self._timers[0][0] - time.time()))
            rl = list(self._listeners) + list(self._udp)
            wl = list(self._connecting)
            for c in list(self._conns):
                if c._want_read():
                    rl.append(c.sock)
                if c._want_write():
                    wl.append(c.sock)
            if not rl and not wl:
                time.sleep(timeout)
                continue
            try:
                r, w, _ = select.select(rl, wl, [], timeout)
            except (select.error, socket.error, ValueError):
                continue
            bysock = {}
            for c in list(self._conns):
                bysock[c.sock] = c
            for s in r:
                if s in self._listeners:
                    try:
                        cs, addr = s.accept()
                    except socket.error:
                        continue
                    c = Conn(self, cs)
                    cb = self._listeners[s]
                    self._safe(lambda: cb(c, addr[0]))
                elif s in self._udp:
                    try:
                        data, addr = s.recvfrom(65536)
                    except socket.error:
                        continue
                    cb = self._udp[s]
                    self._safe(lambda: cb(data, addr[0]))
                elif s in bysock and bysock[s]._want_read():
                    self._safe(bysock[s]._do_read)
            for s in w:
                if s in self._connecting:
                    on_ok, on_err = self._connecting.pop(s)
                    err = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if err:
                        s.close()
                        self._safe(lambda: on_err("connect failed: %s" % errno.errorcode.get(err, err)))
                    else:
                        c = Conn(self, s)
                        self._safe(lambda: on_ok(c))
                elif s in bysock and bysock[s]._want_write():
                    self._safe(bysock[s]._do_write)

    def _safe(self, f):
        try:
            f()
        except Exception:
            import traceback
            traceback.print_exc()
