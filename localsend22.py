# localsend22: LocalSend protocol v2 (plain HTTP) for Python 2.2 / PyS60 1.4.5.
#
# Extracted from RetroSend. Transport-independent: Server and Client are driven
# by a "net" object (see s60net.py for PyS60, tests/pcnet.py for a PC one) and
# report to an "events" object (log, peer, progress, finished, ...).
# Written for Python 2.2: no enumerate/sorted/sets/generators, no conditional
# expressions, no "with", no "except X as e". Also runs on Python 2.7.

import sys
import os
import time
import random
import types
import struct
import socket

API = "/api/localsend/v2/"
MULTICAST = "224.0.0.167"
DEFAULT_PORT = 53317

def err_text():
    e = sys.exc_info()[1]
    try:
        return unicode(str(e), 'utf-8', 'replace')
    except:
        return u"error"


def to_u(s):
    if type(s) is types.UnicodeType:
        return s
    if s is None:
        return u""
    try:
        return unicode(s, 'utf-8')
    except:
        return unicode(s, 'latin-1')


def to_b(s):
    if type(s) is types.UnicodeType:
        return s.encode('utf-8')
    return str(s)


# ---------------------------------------------------------------- JSON

class Raw:
    """A literal JSON token such as true/false (Python 2.2 has no bool type)."""
    def __init__(self, text):
        self.text = text

JTRUE = Raw("true")
JFALSE = Raw("false")


def _quote(s):
    out = [u'"']
    for ch in s:
        o = ord(ch)
        if ch == u'"':
            out.append(u'\\"')
        elif ch == u'\\':
            out.append(u'\\\\')
        elif ch == u'\n':
            out.append(u'\\n')
        elif ch == u'\r':
            out.append(u'\\r')
        elif ch == u'\t':
            out.append(u'\\t')
        elif o < 32:
            out.append(u'\\u%04x' % o)
        else:
            out.append(ch)
    out.append(u'"')
    return u"".join(out)


_BOOL = type(1 == 1)  # int on Python 2.2, bool on newer versions


def _dump(o):
    t = type(o)
    if o is None:
        return u"null"
    if _BOOL is not types.IntType and t is _BOOL:
        if o:
            return u"true"
        return u"false"
    if isinstance(o, Raw):
        return unicode(o.text)
    if t is types.UnicodeType:
        return _quote(o)
    if t is types.StringType:
        return _quote(to_u(o))
    if t is types.DictType:
        parts = []
        for k in o.keys():
            parts.append(_quote(to_u(k)) + u":" + _dump(o[k]))
        return u"{" + u",".join(parts) + u"}"
    if t is types.ListType or t is types.TupleType:
        parts = []
        for v in o:
            parts.append(_dump(v))
        return u"[" + u",".join(parts) + u"]"
    if t is types.FloatType:
        return unicode(repr(o))
    return unicode(str(o))


def json_dumps(o):
    return _dump(o).encode('utf-8')


class _Parser:
    def __init__(self, s):
        self.s = s
        self.i = 0

    def ws(self):
        s = self.s
        n = len(s)
        while self.i < n and s[self.i] in u" \t\r\n":
            self.i = self.i + 1

    def value(self):
        self.ws()
        s = self.s
        if self.i >= len(s):
            raise ValueError("unexpected end")
        c = s[self.i]
        if c == u'{':
            return self.obj()
        if c == u'[':
            return self.arr()
        if c == u'"':
            return self.string()
        if s[self.i:self.i + 4] == u"true":
            self.i = self.i + 4
            return True
        if s[self.i:self.i + 5] == u"false":
            self.i = self.i + 5
            return False
        if s[self.i:self.i + 4] == u"null":
            self.i = self.i + 4
            return None
        return self.number()

    def obj(self):
        d = {}
        self.i = self.i + 1
        self.ws()
        if self.s[self.i] == u'}':
            self.i = self.i + 1
            return d
        while 1:
            self.ws()
            k = self.string()
            self.ws()
            if self.s[self.i] != u':':
                raise ValueError("expected :")
            self.i = self.i + 1
            v = self.value()
            d[k] = v
            self.ws()
            c = self.s[self.i]
            self.i = self.i + 1
            if c == u'}':
                return d
            if c != u',':
                raise ValueError("bad object")

    def arr(self):
        a = []
        self.i = self.i + 1
        self.ws()
        if self.s[self.i] == u']':
            self.i = self.i + 1
            return a
        while 1:
            a.append(self.value())
            self.ws()
            c = self.s[self.i]
            self.i = self.i + 1
            if c == u']':
                return a
            if c != u',':
                raise ValueError("bad array")

    def string(self):
        s = self.s
        if s[self.i] != u'"':
            raise ValueError("expected string")
        self.i = self.i + 1
        out = []
        while 1:
            c = s[self.i]
            self.i = self.i + 1
            if c == u'"':
                return u"".join(out)
            if c != u'\\':
                out.append(c)
                continue
            e = s[self.i]
            self.i = self.i + 1
            if e == u'n':
                out.append(u'\n')
            elif e == u't':
                out.append(u'\t')
            elif e == u'r':
                out.append(u'\r')
            elif e == u'b':
                out.append(u'\b')
            elif e == u'f':
                out.append(u'\f')
            elif e == u'u':
                out.append(unichr(int(s[self.i:self.i + 4], 16)))
                self.i = self.i + 4
            else:
                out.append(e)

    def number(self):
        s = self.s
        start = self.i
        real = 0
        while self.i < len(s) and s[self.i] in u"0123456789+-.eE":
            if s[self.i] in u".eE":
                real = 1
            self.i = self.i + 1
        t = s[start:self.i]
        if not t:
            raise ValueError("bad value")
        if real:
            return float(t)
        return long(t)


def json_loads(s):
    return _Parser(to_u(s)).value()


def jget(d, key, default=None):
    if type(d) is not types.DictType:
        return default
    if d.has_key(key) and d[key] is not None:
        return d[key]
    return default


# ---------------------------------------------------------------- URLs

_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~"


def url_quote(s):
    out = []
    for ch in to_b(s):
        if _SAFE.find(ch) >= 0:
            out.append(ch)
        else:
            out.append("%%%02X" % ord(ch))
    return "".join(out)


def url_unquote(s):
    s = s.replace("+", " ")
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "%" and i + 2 < n:
            try:
                out.append(chr(int(s[i + 1:i + 3], 16)))
                i = i + 3
                continue
            except:
                pass
        out.append(ch)
        i = i + 1
    return "".join(out)


def parse_query(q):
    d = {}
    for part in q.split("&"):
        if not part:
            continue
        i = part.find("=")
        if i >= 0:
            d[url_unquote(part[:i])] = url_unquote(part[i + 1:])
        else:
            d[url_unquote(part)] = ""
    return d


REASONS = {200: "OK", 204: "No Content", 400: "Bad Request", 401: "Unauthorized",
           403: "Forbidden", 404: "Not Found", 409: "Conflict", 500: "Internal Server Error"}



# ---------------------------------------------------------------- helpers

MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
        "bmp": "image/bmp", "mp3": "audio/mpeg", "aac": "audio/aac", "m4a": "audio/mp4",
        "amr": "audio/amr", "wav": "audio/wav", "wma": "audio/x-ms-wma", "mp4": "video/mp4",
        "3gp": "video/3gpp", "txt": "text/plain", "pdf": "application/pdf",
        "zip": "application/zip", "jar": "application/java-archive",
        "sis": "application/vnd.symbian.install", "sisx": "x-epoc/x-sisx-app",
        "vcf": "text/vcard", "apk": "application/vnd.android.package-archive",
        "py": "text/x-python"}


def mime_type(name):
    name = to_u(name).lower()
    i = name.rfind(u".")
    if i < 0:
        return "application/octet-stream"
    return MIME.get(str(name[i + 1:].encode('ascii', 'replace')), "application/octet-stream")


def human_size(n):
    n = long(n)
    if n < 1024:
        return u"%d B" % n
    if n < 1048576:
        return u"%d KB" % (n / 1024)
    return u"%.1f MB" % (n / 1048576.0)


def safe_name(name):
    name = to_u(name)
    out = []
    for ch in name:
        if ord(ch) < 32 or u'/\\:*?"<>|'.find(ch) >= 0:
            out.append(u'_')
        else:
            out.append(ch)
    s = u"".join(out).strip()
    while s[:1] == u".":
        s = s[1:]
    if not s:
        s = u"file"
    if len(s) > 120:
        i = s.rfind(u".")
        ext = u""
        if i > 0 and len(s) - i <= 10:
            ext = s[i:]
        s = s[:120 - len(ext)] + ext
    return s


def random_id():
    # Small ranges only: on Python 2.2 randint(0, 0x7fffffff) overflows
    # ("long int too large to convert to int").
    parts = []
    for k in range(4):
        parts.append("%04x" % random.randrange(0, 0x10000))
    return "".join(parts)


def make_info(cfg, with_net=1, announce=0):
    d = {"alias": to_u(cfg["alias"]), "version": "2.0",
         "deviceModel": to_u(cfg.get("model", u"S60 phone")), "deviceType": "mobile",
         "fingerprint": cfg["fingerprint"], "download": JFALSE}
    if with_net:
        d["port"] = cfg["port"]
        d["protocol"] = "http"
    if announce:
        d["announce"] = JTRUE
    return d


def make_dev(info, ip):
    return {"alias": to_u(jget(info, "alias", u"Unknown")), "ip": ip,
            "port": int(jget(info, "port", DEFAULT_PORT)),
            "fingerprint": to_u(jget(info, "fingerprint", u"")),
            "deviceModel": jget(info, "deviceModel"),
            "protocol": to_u(jget(info, "protocol", u"https"))}


class Refused(Exception):
    pass



def make_file_item(path, name=None):
    path = to_b(path)
    if name is None:
        name = to_u(os.path.basename(path.replace("\\", "/")))
    return {"path": path, "name": to_u(name), "size": long(os.path.getsize(path)),
            "type": mime_type(name), "text": None}


def save_dir_for(cfg, name):
    """Folder for a received file: photos/music/videos go where the phone's
    Gallery and Music player look (if "sort" is on), the rest to savedir."""
    if cfg.get("sort"):
        e = to_b(name).lower()
        e = e[e.rfind(".") + 1:]
        kinds = [(["jpg", "jpeg", "png", "gif", "bmp"], ["E:\\Images\\", "C:\\Data\\Images\\"]),
                 (["mp3", "m4a", "aac", "wma", "wav", "amr"], ["E:\\Music\\", "E:\\Sounds\\", "C:\\Data\\Sounds\\"]),
                 (["mp4", "3gp", "m4v", "3g2"], ["E:\\Videos\\", "C:\\Data\\Videos\\"])]
        for exts, dirs in kinds:
            if exts.count(e):
                for d in dirs:
                    if os.path.isdir(d):
                        return d
    return cfg["savedir"]


def make_text_item(text):
    text = to_u(text)
    return {"path": None, "name": u"message.txt", "size": len(text.encode('utf-8')),
            "type": "text/plain", "text": text}



# ---------------------------------------------------------------- async HTTP
#
# On PyS60 1.4.5 sockets only work on the main thread, through callbacks
# (a background thread has no Symbian active scheduler and freezes the app).
# So everything below is non-blocking and driven by a "net" object:
#   net.listen(port, on_conn)            on_conn(conn, ip)
#   net.connect(host, port, on_ok, on_err)
#   net.later(seconds, func)
#   net.udp_send(host, port, data) / net.udp_listen(port, on_packet)
#   conn.recv(on_data)   on_data("") means closed
#   conn.send(data, on_done, on_err) / conn.close() / conn.local_ip()


class BodyDecoder:
    """Incremental body decoder: Content-Length, chunked, or until close."""
    def __init__(self, headers, is_response=0):
        self.mode = "length"
        self.left = 0
        self.pend = ""
        self.cstate = "size"
        if headers.get("transfer-encoding", "").lower().find("chunked") >= 0:
            self.mode = "chunked"
        elif headers.has_key("content-length"):
            self.left = long(headers["content-length"])
        elif is_response:
            self.mode = "close"
        self.done = (self.mode == "length" and self.left == 0)

    def feed(self, data):
        """Returns body bytes contained in data; sets self.done at the end."""
        if self.done:
            return ""
        if self.mode == "close":
            return data
        if self.mode == "length":
            take = data[:int(min(len(data), self.left))]
            self.left = self.left - len(take)
            if self.left <= 0:
                self.done = 1
            return take
        self.pend = self.pend + data
        out = []
        while 1:
            if self.cstate == "size":
                i = self.pend.find("\r\n")
                if i < 0:
                    break
                line = self.pend[:i]
                self.pend = self.pend[i + 2:]
                semi = line.find(";")
                if semi >= 0:
                    line = line[:semi]
                size = long(line.strip() or "0", 16)
                if size == 0:
                    self.cstate = "trailer"
                else:
                    self.left = size
                    self.cstate = "data"
            elif self.cstate == "data":
                if not self.pend:
                    break
                take = self.pend[:int(min(len(self.pend), self.left))]
                self.pend = self.pend[len(take):]
                self.left = self.left - len(take)
                out.append(take)
                if self.left == 0:
                    self.cstate = "crlf"
            elif self.cstate == "crlf":
                if len(self.pend) < 2:
                    break
                self.pend = self.pend[2:]
                self.cstate = "size"
            else:  # trailer
                if self.pend[:2] == "\r\n" or self.pend.find("\r\n\r\n") >= 0:
                    self.done = 1
                    self.pend = ""
                break
        return "".join(out)


def parse_head(text):
    """Splits a header block into (first line, headers dict)."""
    lines = text.split("\r\n")
    h = {}
    for line in lines[1:]:
        i = line.find(":")
        if i > 0:
            h[line[:i].strip().lower()] = line[i + 1:].strip()
    return lines[0], h


def response_bytes(code, body=None):
    data = ""
    head = "HTTP/1.1 %d %s\r\n" % (code, REASONS.get(code, "Status"))
    if body is not None:
        data = json_dumps(body)
        head = head + "Content-Type: application/json; charset=utf-8\r\n"
    return head + "Content-Length: %d\r\nConnection: close\r\n\r\n" % len(data) + data


def report_ip(events, conn):
    f = getattr(events, "got_ip", None)
    if f is not None:
        try:
            f(conn.local_ip())
        except:
            pass


class HttpRequest:
    """One POST request: connect, send headers + body (bytes or a file), read reply."""
    def __init__(self, net, events, host, port, path, ctype, data=None, fpath=None, length=0,
                 on_progress=None, on_done=None, on_err=None):
        self.net = net
        self.ev = events
        self.host = host
        self.port = port
        self.path = path
        self.ctype = ctype
        self.data = data
        self.fpath = fpath
        self.length = length
        self.on_progress = on_progress
        self.on_done = on_done
        self.on_err = on_err
        self.conn = None
        self.f = None
        self.quiet = 0
        self.finished = 0
        self.buf = ""
        self.head = None
        self.code = 0
        self.dec = None
        self.body = []
        if data is not None:
            self.length = len(data)

    def start(self):
        if not self.quiet:
            self.ev.log(u"-> POST %s:%d %s" % (to_u(self.host), self.port,
                                           to_u(self.path.split("?")[0])))
        self.net.connect(self.host, self.port, self.connected, self.fail)

    def cancel(self):
        self.fail("Cancelled")

    def connected(self, conn):
        if self.finished:
            conn.close()
            return
        self.conn = conn
        report_ip(self.ev, conn)
        head = ("POST %s HTTP/1.1\r\nHost: %s:%d\r\nUser-Agent: RetroSend\r\n"
                "Content-Type: %s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                % (self.path, self.host, self.port, self.ctype, self.length))
        if self.data is not None:
            conn.send(head + self.data, self.wait_reply, self.fail)
        else:
            try:
                self.f = open(self.fpath, "rb")
            except:
                self.fail("cannot open file: " + str(sys.exc_info()[1]))
                return
            self.sent = 0
            conn.send(head, self.send_chunk, self.fail)

    def send_chunk(self):
        if self.finished:
            return
        chunk = self.f.read(16384)
        if not chunk:
            self.f.close()
            self.f = None
            if self.sent != self.length:
                self.fail("file changed while sending")
                return
            self.wait_reply()
            return
        self.sent = self.sent + len(chunk)
        if self.on_progress is not None:
            self.on_progress(len(chunk))
        self.conn.send(chunk, self.send_chunk, self.fail)

    def wait_reply(self):
        if not self.finished:
            self.conn.recv(self.got_data)

    def got_data(self, data):
        if self.finished:
            return
        if not data:
            if self.head is not None and self.dec is not None and self.dec.mode == "close":
                self.finish()
            else:
                self.fail("connection closed by " + self.host)
            return
        if self.head is None:
            self.buf = self.buf + data
            i = self.buf.find("\r\n\r\n")
            if i < 0:
                if len(self.buf) > 32768:
                    self.fail("reply header too long")
                    return
                self.conn.recv(self.got_data)
                return
            first, headers = parse_head(self.buf[:i])
            data = self.buf[i + 4:]
            self.buf = ""
            self.head = headers
            parts = first.split(" ")
            try:
                self.code = int(parts[1])
            except:
                self.fail("bad reply")
                return
            self.dec = BodyDecoder(headers, 1)
        self.body.append(self.dec.feed(data))
        if self.dec.done:
            self.finish()
        else:
            self.conn.recv(self.got_data)

    def cleanup(self):
        self.finished = 1
        if self.f is not None:
            try:
                self.f.close()
            except:
                pass
            self.f = None
        if self.conn is not None:
            try:
                self.conn.close()
            except:
                pass

    def finish(self):
        self.cleanup()
        if not self.quiet:
            self.ev.log(u"<- HTTP %d from %s" % (self.code, to_u(self.host)))
        if self.on_done is not None:
            self.on_done(self.code, "".join(self.body))

    def fail(self, msg):
        if self.finished:
            return
        self.cleanup()
        if not self.quiet:
            self.ev.log(u"!! request to %s failed: %s" % (to_u(self.host), to_u(msg)))
        if self.on_err is not None:
            self.on_err(to_u(msg))


# ---------------------------------------------------------------- receiving

class ServerConn:
    """One incoming HTTP connection."""
    def __init__(self, srv, conn, ip):
        self.srv = srv
        self.conn = conn
        self.ip = ip
        self.buf = ""
        self.state = "head"
        self.dec = None
        self.body = []
        self.closed = 0
        self.out = None
        self.upload = None
        report_ip(srv.ev, conn)
        conn.recv(self.got_data)

    def close(self):
        if not self.closed:
            self.closed = 1
            try:
                self.conn.close()
            except:
                pass
        if self.out is not None:
            self.abort_upload(u"connection closed")

    def respond(self, code, body=None):
        if self.closed:
            return
        if code != 200:
            self.srv.ev.log(u"-> answered %d" % code)
        self.state = "done"
        self.conn.send(response_bytes(code, body), self.close, self.send_failed)

    def send_failed(self, msg):
        self.close()

    def got_data(self, data):
        if self.closed:
            return
        if not data:
            self.close()
            return
        try:
            self.process(data)
        except:
            self.srv.ev.log(u"request: " + err_text())
            if self.out is not None:
                self.abort_upload(err_text())
            self.respond(500)

    def process(self, data):
        if self.state == "head":
            self.buf = self.buf + data
            i = self.buf.find("\r\n\r\n")
            if i < 0:
                if len(self.buf) > 32768:
                    self.respond(400)
                else:
                    self.conn.recv(self.got_data)
                return
            first, headers = parse_head(self.buf[:i])
            data = self.buf[i + 4:]
            self.buf = ""
            parts = first.split(" ")
            if len(parts) < 2:
                self.respond(400)
                return
            self.method = parts[0]
            target = parts[1]
            web = getattr(self.srv, "web", None)
            if web is not None and target[:len("/api/")] != "/api/":
                self.state = "handed"  # browser sharing owns this connection now
                web.handle(self.conn, self.ip, self.method, target, headers, data)
                return
            self.query = {}
            q = target.find("?")
            if q >= 0:
                self.query = parse_query(target[q + 1:])
                target = target[:q]
            if target[:len("/api/localsend/v1/")] == "/api/localsend/v1/":
                target = API + target[len("/api/localsend/v1/"):]
            if target[:len(API)] != API:
                self.respond(404)
                return
            self.route = target[len(API):]
            self.srv.ev.log(u"<- %s %s from %s" % (to_u(self.method), to_u(self.route),
                                                   to_u(self.ip)))
            self.dec = BodyDecoder(headers, 0)
            if self.route == "upload" and self.method == "POST":
                if not self.srv.begin_upload(self):
                    return  # already answered
                self.state = "upload"
            else:
                self.state = "body"
        if self.state == "body":
            self.body.append(self.dec.feed(data))
            if self.dec.done:
                self.state = "dispatch"
                self.srv.dispatch(self, "".join(self.body))
            else:
                self.conn.recv(self.got_data)
        elif self.state == "upload":
            chunk = self.dec.feed(data)
            if chunk:
                self.out.write(chunk)
                self.srv.upload_progress(self, len(chunk))
            if self.dec.done:
                self.srv.end_upload(self)
            else:
                self.conn.recv(self.got_data)

    def abort_upload(self, why):
        self.srv.abort_upload(self, why)


class Server:
    def __init__(self, cfg, events, net):
        self.cfg = cfg
        self.ev = events
        self.net = net
        self.session = None
        self.running = 0

    def start(self):
        self.net.listen(self.cfg["port"], self.incoming)
        self.running = 1
        self.ev.log(u"listening on port %d" % self.cfg["port"])

    def stop(self):
        self.running = 0
        try:
            self.net.stop_listen()
        except:
            pass

    def incoming(self, conn, ip):
        if not self.running:
            conn.close()
            return
        ServerConn(self, conn, ip)

    def dispatch(self, sc, body):
        r = sc.route
        if r == "info":
            sc.respond(200, make_info(self.cfg, 0))
        elif r == "register" and sc.method == "POST":
            try:
                dev = make_dev(json_loads(body), sc.ip)
            except:
                sc.respond(400)
                return
            if dev["fingerprint"] != to_u(self.cfg["fingerprint"]):
                self.ev.peer(dev)
            sc.respond(200, make_info(self.cfg, 0))
        elif r == "prepare-upload" and sc.method == "POST":
            self.prepare(sc, body)
        elif r == "cancel" and sc.method == "POST":
            if self.session is not None:
                self.end_session()
                self.ev.finished(u"The sender cancelled.")
            sc.respond(200)
        else:
            sc.respond(404)

    def receiving(self):
        s = self.session
        return s is not None and s.get("id") is not None

    def stop_receiving(self):
        """User pressed Stop receiving: drop the transfer and partial files."""
        if not self.receiving():
            return 0
        self.end_session()  # closes the uploads; their partial files are deleted
        self.ev.finished(u"Receiving stopped.")
        return 1

    def end_session(self):
        s = self.session
        self.session = None
        if s is not None:
            for sc in s["active"].values():
                try:
                    sc.close()
                except:
                    pass

    def prepare(self, sc, body):
        try:
            req = json_loads(body)
            files = jget(req, u"files")
            dev = make_dev(jget(req, u"info"), sc.ip)
        except:
            sc.respond(400)
            return
        if type(files) is not types.DictType or not files:
            sc.respond(400)
            return
        self.ev.peer(dev)
        if self.session is not None:
            if time.time() - self.session["touched"] < 120:
                sc.respond(409)
                return
            self.end_session()
        flist = files.values()
        if len(flist) == 1:
            f = flist[0]
            ftype = to_u(jget(f, u"fileType", u""))
            if jget(f, u"preview") is not None and (ftype[:5] == u"text/" or ftype == u"text"):
                self.ev.text_received(dev, to_u(jget(f, u"preview")))
                sc.respond(204)
                return
        total = 0
        for f in flist:
            total = total + long(jget(f, u"size", 0))
        # reserve the session while the user decides
        self.session = {"id": None, "touched": time.time(), "active": {}}
        pending = self.session

        def answer(ok, self=self, sc=sc, files=files, dev=dev, total=total, pending=pending):
            if self.session is not pending:
                sc.respond(409)
                return
            if not ok:
                self.session = None
                sc.respond(403)
                return
            sid = random_id()
            tokens = {}
            for fid in files.keys():
                tokens[fid] = random_id()
            pending.update({"id": sid, "ip": sc.ip, "tokens": tokens, "files": files,
                            "done": {}, "total": total, "got": 0, "last": 0,
                            "touched": time.time(), "from": dev["alias"]})
            sc.respond(200, {"sessionId": sid, "files": tokens})
            f = getattr(self.ev, "receive_start", None)
            if f is not None:
                f(dev, len(files), total)
            self.ev.progress(u"Receiving from " + dev["alias"], 0, total)

        self.ev.ask_accept(dev, flist, total, answer)

    def begin_upload(self, sc):
        q = sc.query
        sid = q.get("sessionId")
        fid = to_u(q.get("fileId", ""))
        token = q.get("token")
        s = self.session
        if sid is None or token is None or not fid:
            sc.respond(400)
            return 0
        if s is None or s["id"] is None:
            sc.respond(403)
            return 0
        if s["id"] != sid:
            sc.respond(409)
            return 0
        if s["tokens"].get(fid) != token or s["ip"] != sc.ip or s["done"].has_key(fid):
            sc.respond(403)
            return 0
        f = s["files"][fid]
        name = safe_name(jget(f, u"fileName", u"file"))
        path = self.create_path(name)
        sc.out = open(path, "wb")
        sc.upload = {"fid": fid, "name": name, "path": path, "got": 0,
                     "expected": long(jget(f, u"size", -1)), "session": s}
        s["active"][fid] = sc
        s["touched"] = time.time()
        if sc.dec.done:  # empty file
            self.end_upload(sc)
            return 0
        return 1

    def upload_progress(self, sc, n):
        u = sc.upload
        s = u["session"]
        u["got"] = u["got"] + n
        s["got"] = s["got"] + n
        s["touched"] = time.time()
        if s["got"] - s["last"] > 65536:
            s["last"] = s["got"]
            self.ev.progress(u["name"], s["got"], s["total"])

    def abort_upload(self, sc, why):
        u = sc.upload
        if sc.out is None or u is None:
            return
        try:
            sc.out.close()
        except:
            pass
        sc.out = None
        try:
            os.remove(u["path"])
        except:
            pass
        s = u["session"]
        try:
            del s["active"][u["fid"]]
        except:
            pass
        self.ev.log(u"receive " + u["name"] + u": " + to_u(why))
        if self.session is s:
            self.session = None
            self.ev.finished(u"Receiving failed: " + u["name"])

    def end_upload(self, sc):
        u = sc.upload
        s = u["session"]
        sc.out.close()
        sc.out = None
        try:
            del s["active"][u["fid"]]
        except:
            pass
        if u["expected"] >= 0 and u["got"] != u["expected"]:
            sc.out = open(u["path"], "ab")  # so abort_upload can clean up
            self.abort_upload(sc, u"incomplete file")
            sc.respond(500)
            return
        s["done"][u["fid"]] = 1
        self.ev.log(u"Saved " + to_u(u["path"]))
        f = getattr(self.ev, "saved", None)
        if f is not None:
            f(u["path"])
        sc.respond(200)
        if len(s["done"]) == len(s["tokens"]) and self.session is s:
            self.session = None
            self.ev.finished(u"Received %d file(s) from %s.\nOpen them from Receive > "
                             u"Received files." % (len(s["done"]), s["from"]))

    def create_path(self, name):
        d = save_dir_for(self.cfg, name)
        if not os.path.exists(d):
            os.makedirs(d)
        i = name.rfind(u".")
        base = name
        ext = u""
        if i > 0:
            base = name[:i]
            ext = name[i:]
        n = 0
        while 1:
            cand = name
            if n:
                cand = u"%s (%d)%s" % (base, n, ext)
            path = d + cand.encode('utf-8')
            if not os.path.exists(path):
                return path
            n = n + 1


# ---------------------------------------------------------------- sending

class Client:
    def __init__(self, cfg, events, net):
        self.cfg = cfg
        self.ev = events
        self.net = net
        self.req = None
        self.quiet = 0
        self.cancelled = 0

    def post(self, dev_ip, port, path, ctype, data, on_done, on_err, fpath=None, length=0,
             on_progress=None):
        r = HttpRequest(self.net, self.ev, dev_ip, port, path, ctype, data, fpath, length,
                        on_progress, on_done, on_err)
        r.quiet = self.quiet
        self.req = r
        r.start()

    def cancel(self):
        self.cancelled = 1
        if self.req is not None:
            self.req.cancel()

    def register(self, host, port, on_ok, on_err):
        def done(code, text, host=host, port=port, on_ok=on_ok, on_err=on_err):
            if code != 200:
                on_err(u"HTTP %d" % code)
                return
            try:
                dev = make_dev(json_loads(text), host)
            except:
                on_err(u"bad reply")
                return
            dev["port"] = port
            dev["protocol"] = u"http"
            on_ok(dev)
        self.post(host, port, API + "register", "application/json",
                  json_dumps(make_info(self.cfg)), done, on_err)

    def send(self, dev, items, on_done, on_err):
        self.cancelled = 0
        files = {}
        total = 0
        only_text = 1
        k = 0
        for it in items:
            fid = "f%d" % k
            k = k + 1
            f = {"id": fid, "fileName": it["name"], "size": it["size"], "fileType": it["type"]}
            if it["text"] is not None:
                f["preview"] = it["text"]
            else:
                only_text = 0
            files[fid] = f
            total = total + it["size"]
        st = {"dev": dev, "items": items, "total": total, "sent": 0, "index": 0,
              "done": 0, "last": 0, "tokens": {}, "sid": "",
              "on_done": on_done, "on_err": on_err}
        self.st = st
        self.ev.progress(u"Waiting for " + dev["alias"] + u" to accept", 0, total)

        def prepared(code, text, self=self, st=st, only_text=only_text):
            a = st["dev"]["alias"]
            if code == 204:
                if only_text:
                    st["on_done"](u"Message sent to " + a)
                else:
                    st["on_done"](u"Nothing needed to be sent.")
                return
            if code != 200:
                msgs = {401: a + u" needs a PIN. Turn off \"Require PIN\" in LocalSend.",
                        403: a + u" declined.",
                        409: a + u" is busy with another transfer."}
                st["on_err"](msgs.get(code, a + u" replied with error %d." % code))
                return
            try:
                res = json_loads(text)
                st["sid"] = to_b(jget(res, u"sessionId", u""))
                st["tokens"] = jget(res, u"files", {})
            except:
                st["on_err"](u"bad reply from " + a)
                return
            self.next_file()

        self.post(dev["ip"], dev["port"], API + "prepare-upload", "application/json",
                  json_dumps({"info": make_info(self.cfg), "files": files}),
                  prepared, self.failed)

    def failed(self, msg):
        st = self.st
        if self.cancelled:
            self.cancelled = 0
            if st["sid"]:
                d = st["dev"]
                ignore = lambda *a: None
                self.post(d["ip"], d["port"], API + "cancel?sessionId=" + url_quote(st["sid"]),
                          "application/json", "", ignore, ignore)
            st["on_err"](u"Cancelled.")
            return
        st["on_err"](msg)

    def next_file(self):
        st = self.st
        items = st["items"]
        while st["index"] < len(items):
            i = st["index"]
            it = items[i]
            st["index"] = i + 1
            fid = "f%d" % i
            token = st["tokens"].get(to_u(fid))
            if token is None:
                continue  # receiver skipped this one
            d = st["dev"]
            path = (API + "upload?sessionId=" + url_quote(st["sid"]) + "&fileId=" + fid +
                    "&token=" + url_quote(to_b(token)))

            def uploaded(code, text, self=self, st=st, name=it["name"]):
                if code != 200:
                    self.failed(u"upload of %s failed: HTTP %d" % (name, code))
                    return
                st["sent"] = st["sent"] + 1
                self.next_file()

            if it["text"] is not None:
                self.post(d["ip"], d["port"], path, "text/plain", it["text"].encode('utf-8'),
                          uploaded, self.failed)
            else:
                self.post(d["ip"], d["port"], path, "application/octet-stream", None,
                          uploaded, self.failed, it["path"], it["size"], self.sent_bytes)
            return
        st["on_done"](u"Sent %d file(s) to %s" % (st["sent"], st["dev"]["alias"]))

    def sent_bytes(self, n):
        st = self.st
        st["done"] = st["done"] + n
        if st["done"] - st["last"] > 65536 or st["done"] >= st["total"]:
            st["last"] = st["done"]
            self.ev.progress(u"Sending", st["done"], st["total"])

    def announce(self, local_ip=None):
        msg = json_dumps(make_info(self.cfg, 1, 1))
        targets = [MULTICAST]
        if local_ip:
            targets.append(local_ip[:local_ip.rfind(".")] + ".255")
        for t in targets:
            try:
                self.net.udp_send(t, DEFAULT_PORT, msg)
            except:
                self.ev.log(u"announce " + to_u(t) + u": " + err_text())


# ---------------------------------------------------------------- phone network
