# -*- coding: utf-8 -*-
"""PC test for localsend22.py against a fake LocalSend v2 peer.

Run with Python 2.7:  python2 tests/test_pys60.py
"""
import BaseHTTPServer
import SocketServer
import hashlib
import httplib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib
import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(1, os.path.dirname(HERE))

import localsend22 as retrosend
import pcnet  # noqa: E402

NET = pcnet.SelectNet()


def start_loop():
    t = threading.Thread(target=NET.run)
    t.setDaemon(True)
    t.start()


def on_loop(func, timeout=30):
    """Runs func(done) on the loop thread; done(value) finishes. Returns value or raises."""
    box = {}
    ev = threading.Event()

    def done(*a):
        box["v"] = a
        ev.set()

    NET.call(lambda: func(done))
    if not ev.wait(timeout):
        raise RuntimeError("timed out on loop")
    return box["v"]

INFO = {"alias": "Fake PC", "version": "2.1", "deviceModel": "Linux", "deviceType": "desktop",
        "fingerprint": "fakefp", "port": 53401, "protocol": "http", "download": False}
fails = []


def check(name, cond):
    if cond:
        print("PASS " + name)
    else:
        print("FAIL " + name)
        fails.append(name)


class Events(object):
    def __init__(self):
        self.texts = []
        self.logs = []
        self.finished_msgs = []

    def log(self, msg):
        self.logs.append(msg)

    def peer(self, dev):
        pass

    def ask_accept(self, dev, files, total, answer):
        if dev.get('alias') == u'Reject Me':
            answer(0)
        else:
            answer(1)

    def text_received(self, dev, text):
        self.texts.append((dev.get('alias'), text))

    def progress(self, title, done, total):
        pass

    def finished(self, msg):
        self.finished_msgs.append(msg)


def req(method, path, body=None, headers=None, port=53400):
    c = httplib.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body, headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def q(d):
    return urllib.urlencode(d)


def part1(work):
    recv_dir = os.path.join(work, "received") + "/"
    ev = Events()
    cfg = {'alias': u'Phone', 'fingerprint': 'fp123', 'port': 53400, 'savedir': recv_dir}
    srv = retrosend.Server(cfg, ev, NET)
    on_loop(lambda done: (srv.start(), done()))
    for _ in range(50):
        try:
            req("GET", "/api/localsend/v2/info")
            break
        except Exception:
            time.sleep(0.1)

    s, d = req("POST", "/api/localsend/v2/register", json.dumps(INFO))
    check("register 200", s == 200 and json.loads(d)["alias"] == u"Phone")
    s, d = req("GET", "/api/localsend/v2/info")
    check("info", s == 200 and json.loads(d)["fingerprint"] == u"fp123")

    blob = os.urandom(300000)
    name = u"Holiday photo \xe9.jpg"
    prep = {"info": INFO, "files": {
        "a": {"id": "a", "fileName": name, "size": len(blob), "fileType": "image/jpeg"},
        "b": {"id": "b", "fileName": "../evil/x.txt", "size": 5, "fileType": "text/plain"}}}
    s, d = req("POST", "/api/localsend/v2/prepare-upload", json.dumps(prep))
    check("prepare 200", s == 200)
    res = json.loads(d)
    sid, toks = res["sessionId"], res["files"]
    s, _ = req("POST", "/api/localsend/v2/prepare-upload", json.dumps(prep))
    check("second session blocked 409", s == 409)
    s, _ = req("POST", "/api/localsend/v2/upload?" + q({"sessionId": sid, "fileId": "a", "token": "bad"}), blob)
    check("bad token 403", s == 403)
    s, _ = req("POST", "/api/localsend/v2/upload?" + q({"sessionId": sid, "fileId": "a", "token": toks["a"]}), blob)
    check("upload a 200", s == 200)

    c = httplib.HTTPConnection("127.0.0.1", 53400, timeout=10)
    c.putrequest("POST", "/api/localsend/v2/upload?" + q({"sessionId": sid, "fileId": "b", "token": toks["b"]}))
    c.putheader("Transfer-Encoding", "chunked")
    c.endheaders()
    for p in ("he", "llo"):
        c.send("%x\r\n%s\r\n" % (len(p), p))
    c.send("0\r\n\r\n")
    check("chunked upload b 200", c.getresponse().status == 200)
    c.close()
    time.sleep(0.3)

    path_a = os.path.join(recv_dir, name.encode('utf-8'))
    got = os.path.exists(path_a) and open(path_a, "rb").read() or ""
    check("file a bytes identical (UTF-8 name)", hashlib.sha256(got).digest() == hashlib.sha256(blob).digest())
    path_b = os.path.join(recv_dir, "_evil_x.txt")
    check("file b sanitized name", os.path.exists(path_b) and open(path_b, "rb").read() == "hello")

    msg = {"info": INFO, "files": {"m": {"id": "m", "fileName": "m.txt", "size": 5,
                                         "fileType": "text/plain", "preview": u"Hi phone ✓"}}}
    s, _ = req("POST", "/api/localsend/v2/prepare-upload", json.dumps(msg))
    check("text message 204", s == 204)
    time.sleep(0.2)
    check("server saw text", (u"Fake PC", u"Hi phone ✓") in ev.texts)

    rej = dict(prep)
    rej["info"] = dict(INFO, alias="Reject Me")
    s, _ = req("POST", "/api/localsend/v2/prepare-upload", json.dumps(rej))
    check("declined 403", s == 403)

    s, d = req("POST", "/api/localsend/v2/prepare-upload", json.dumps(prep))
    sid2 = s == 200 and json.loads(d)["sessionId"] or ""
    s, _ = req("POST", "/api/localsend/v2/cancel?sessionId=" + sid2)
    check("cancel 200", s == 200)
    s, _ = req("POST", "/api/localsend/v2/prepare-upload", json.dumps(prep))
    check("new session after cancel 200", s == 200)
    try:
        on_loop(lambda done: (srv.stop(), done()))
    except Exception:
        pass


received = {}


class H(BaseHTTPServer.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def reply(self, code, obj=None):
        data = obj is not None and json.dumps(obj) or ""
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        u = urlparse.urlparse(self.path)
        qs = dict(urlparse.parse_qsl(u.query))
        if u.path == "/api/localsend/v2/register":
            received["register"] = json.loads(self.body())
            self.reply(200, INFO)
        elif u.path == "/api/localsend/v2/prepare-upload":
            p = json.loads(self.body())
            received.setdefault("prepare", []).append(p)
            f = list(p["files"].values())[0]
            if f.get("preview"):
                received["text"] = f["preview"]
                self.reply(204)
            else:
                self.reply(200, {"sessionId": "S1", "files": {f["id"]: "T1"}})
        elif u.path == "/api/localsend/v2/upload":
            ok = qs.get("sessionId") == "S1" and qs.get("token") == "T1"
            received["upload"] = self.body()
            self.reply(ok and 200 or 403)
        else:
            self.reply(404)


class TServer(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def part2(work):
    httpd = TServer(("127.0.0.1", 53401), H)
    t = threading.Thread(target=httpd.serve_forever)
    t.setDaemon(True)
    t.start()
    src = os.path.join(work, "song.mp3")
    data = os.urandom(123457)
    open(src, "wb").write(data)
    ev = Events()
    cfg = {'alias': u'Phone', 'fingerprint': 'fp123', 'port': 53400, 'savedir': work + "/"}
    cl = retrosend.Client(cfg, ev, NET)

    def call_cl(fn):
        r = on_loop(lambda done: fn(lambda v: done("ok", v), lambda m: done("err", m)))
        if r[0] != "ok":
            raise RuntimeError("client error: %r" % (r[1],))
        return r[1]
    try:
        dev = call_cl(lambda ok, err: cl.register("127.0.0.1", 53401, ok, err))
        check("client registered", received.get("register", {}).get("alias") == u"Phone"
              and dev.get("alias") == u"Fake PC")
        item = retrosend.make_file_item(src)
        r1 = call_cl(lambda ok, err: cl.send(dev, [item], ok, err))
        print("RESULT " + repr(r1))
        titem = retrosend.make_text_item(u"Hello from phone")
        r2 = call_cl(lambda ok, err: cl.send(dev, [titem], ok, err))
        print("RESULT " + repr(r2))
    except Exception:
        import traceback
        traceback.print_exc()
        check("client ran without error", False)
    prep = received.get("prepare") or [{"files": {"x": {}}, "info": {}}]
    pf = list(prep[0]["files"].values())[0]
    check("prepare metadata", pf.get("fileName") == u"song.mp3" and pf.get("size") == len(data)
          and pf.get("fileType") == u"audio/mpeg" and prep[0]["info"].get("protocol") == u"http")
    check("uploaded bytes identical", received.get("upload") == data)
    check("text sent", received.get("text") == u"Hello from phone")
    httpd.shutdown()


def part0():
    s = retrosend.json_dumps({"a": [1, u"\xe9", True, None], "b": {"c": "d"}})
    check("json_dumps returns str", isinstance(s, str))
    o = retrosend.json_loads(s)
    check("json roundtrip", o == {u"a": [1, u"\xe9", True, None], u"b": {u"c": u"d"}})
    check("json_loads gives unicode", isinstance(retrosend.json_loads('"x"'), unicode))


def main():
    work = tempfile.mkdtemp(prefix="localsend22_test_")
    start_loop()
    try:
        part0()
        part1(work)
        part2(work)
    finally:
        shutil.rmtree(work, True)
    if fails:
        print("\nFAILED: " + ", ".join(fails))
        sys.exit(1)
    print("\nALL PASSED")


if __name__ == "__main__":
    main()
