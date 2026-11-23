# s60net: PyS60 1.4.5 backend ("net" object) for localsend22.
# Callback-based sockets on the main thread (no threads: PyS60 has no active
# scheduler on other threads). Needs the e32 module, so it only runs on a phone.
#
# `app` must provide: safe(func, *args) (call func, log exceptions) and log(unicode).
import sys
import types
import socket
import e32
from localsend22 import to_u, err_text

def raw(sock):
    """The e32socket object under the socket module's wrapper (takes callbacks)."""
    return getattr(sock, "_sock", sock)


def valid_ip(ip):
    """PyS60's accept() reports a wrong address like 0.8.0.0 for incoming
    connections, so devices are only listed with an address we contacted."""
    ip = to_b(ip or "")
    return ip.count(".") == 3 and ip[:2] != "0." and ip[:4] != "127."


def is_err(res):
    """e32socket callbacks get (exception_class, value) when an operation fails."""
    return (type(res) is types.TupleType and len(res) == 2 and
            type(res[0]) in (types.ClassType, types.TypeType))


# IMPORTANT (PyS60 e32socket): socket.close() also stops the Wi-Fi
# connection and closes the shared socket server, which breaks every other
# socket and crashes the app. So sockets are never close()d while running:
# connected ones get shutdown(2) and are then simply dropped (freeing a
# socket object closes only that socket).


class PhoneConn:
    def __init__(self, sock, app):
        self.sock = sock
        self.app = app

    def recv(self, on_data):
        def cb(res, on_data=on_data, app=self.app):
            if is_err(res) or res is None:
                res = ""
            app.safe(on_data, res)
        try:
            if self.sock is None:
                raise IOError("closed")
            self.sock.recv(16384, 0, cb)
        except:
            self.app.net.later(0, lambda on_data=on_data: on_data(""))

    def send(self, data, on_done, on_err):
        state = {"data": data}

        def cb(res, self=self, state=state, on_done=on_done, on_err=on_err):
            if is_err(res):
                self.app.safe(on_err, str(res[1]))
                return
            n = len(state["data"])
            try:
                if type(res) in (types.IntType, types.LongType) and 0 < res < n:
                    n = int(res)
            except:
                pass
            state["data"] = state["data"][n:]
            if state["data"]:
                go()
            else:
                self.app.safe(on_done)

        def go(self=self, state=state, cb=cb, on_err=on_err):
            try:
                if self.sock is None:
                    raise IOError("closed")
                self.sock.send(state["data"], 0, cb)
            except:
                self.app.safe(on_err, str(sys.exc_info()[1]))
        go()

    def close(self):
        s = self.sock
        self.sock = None
        if s is not None:
            try:
                s.shutdown(2)
            except:
                pass

    def local_ip(self):
        try:
            return self.sock.getsockname()[0]
        except:
            return None


class PhoneNet:
    """Callback-based sockets on the main thread (no threads at all)."""
    def __init__(self, app):
        self.app = app
        self.timers = []
        self.lsock = None

    def later(self, seconds, func):
        t = e32.Ao_timer()
        box = [t]

        def fire(self=self, box=box, func=func):
            try:
                self.timers.remove(box[0])
            except:
                pass
            self.app.safe(func)
        self.timers.append(t)
        t.after(seconds, fire)

    def listen(self, port, on_conn):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except:
            pass
        s.bind(("0.0.0.0", port))
        s.listen(5)
        self.lsock_wrapper = s
        self.lsock = raw(s)
        self.on_conn = on_conn
        self.arm_accept()

    def arm_accept(self):
        try:
            self.lsock.accept(self.accepted)
        except:
            self.app.log(u"accept: " + err_text())
            self.later(2, self.arm_accept)

    def accepted(self, res):
        if is_err(res):
            self.app.log(u"accept: " + to_u(str(res[1])))
            if self.lsock is not None:
                self.later(1, self.arm_accept)
            return
        sock = res[0]
        addr = res[1]
        self.arm_accept()
        self.app.safe(self.on_conn, PhoneConn(sock, self.app), addr[0])

    def stop_listen(self):
        u = getattr(self, "usock", None)
        self.usock = None
        if u is not None:
            try:
                u.close()
            except:
                pass
        s = self.lsock
        self.lsock = None
        if s is not None:
            try:
                s.close()
            except:
                pass

    def connect(self, host, port, on_ok, on_err):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except:
            self.later(0, lambda on_err=on_err: on_err(err_text()))
            return

        def cb(res, self=self, s=s, on_ok=on_ok, on_err=on_err):
            if is_err(res):
                # no close() here, see the note above PhoneConn
                self.app.safe(on_err, to_u(str(res[1])))
                return
            self.app.safe(on_ok, PhoneConn(raw(s), self.app))
        try:
            raw(s).connect((host, port), cb)
        except:
            msg = err_text()
            self.later(0, lambda on_err=on_err, msg=msg: on_err(msg))

    def udp_send(self, host, port, data):
        # one reusable sending socket (never closed, see the note above)
        u = getattr(self, "usend", None)
        if u is None:
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                u.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except:
                pass
            self.usend = u
        try:
            u.sendto(data, (host, port))
        except:
            self.usend = None
            raise

    def udp_listen(self, port, on_packet):
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.bind(("0.0.0.0", port))
        self.usock_wrapper = u
        self.usock = raw(u)
        self.on_packet = on_packet
        self.arm_udp()

    def arm_udp(self):
        try:
            self.usock.recvfrom(4096, 0, self.got_udp)
        except:
            self.app.log(u"udp: " + err_text())

    def got_udp(self, res):
        if is_err(res):
            self.app.log(u"udp: " + to_u(str(res[1])))
            self.later(2, self.arm_udp)
            return
        data = res[0]
        addr = res[1]
        self.arm_udp()
        self.app.safe(self.on_packet, data, addr[0])


