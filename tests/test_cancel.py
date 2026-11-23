# Cancel sending and Stop receiving, on the PC with the async net.
import os, sys, time, json, socket, threading, tempfile, httplib, urllib
import BaseHTTPServer, SocketServer
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import localsend22 as retrosend, pcnet

NET = pcnet.SelectNet()
t = threading.Thread(target=NET.run); t.setDaemon(True); t.start()
fails = []
def check(n, c):
    print((c and "PASS " or "FAIL ") + n)
    if not c: fails.append(n)

class Ev(object):
    def __init__(self): self.fin = []
    def log(self, m): pass
    def peer(self, d): pass
    def ask_accept(self, d, f, total, answer): answer(1)
    def text_received(self, d, t): pass
    def progress(self, *a): pass
    def finished(self, m): self.fin.append(m)

# ---- 1. cancel while sending a big file to a slow receiver
seen = {"cancel": 0, "upload_started": 0}
class Slow(BaseHTTPServer.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, obj=None):
        d = obj is not None and json.dumps(obj) or ""
        self.send_response(code); self.send_header("Content-Length", str(len(d))); self.end_headers(); self.wfile.write(d)
    def do_POST(self):
        p = self.path.split("?")[0]
        if p.endswith("prepare-upload"):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.reply(200, {"sessionId": "S", "files": {"f0": "T"}})
        elif p.endswith("upload"):
            seen["upload_started"] = 1
            try:
                while self.rfile.read(4096):
                    time.sleep(0.05)          # slow receiver
            except Exception:
                pass
        elif p.endswith("cancel"):
            seen["cancel"] = 1
            self.reply(200)
class TS(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer): daemon_threads = True
srv = TS(("127.0.0.1", 53411), Slow)
st = threading.Thread(target=srv.serve_forever); st.setDaemon(True); st.start()

work = tempfile.mkdtemp() + "/"
big = work + "big.bin"; open(big, "wb").write(os.urandom(5 * 1024 * 1024))
cfg = {"alias": u"Phone", "fingerprint": "fp", "port": 53410, "savedir": work + "in/"}
ev = Ev()
cl = retrosend.Client(cfg, ev, NET)
res = {}
done = threading.Event()
dev = {"alias": u"Slow", "ip": "127.0.0.1", "port": 53411, "fingerprint": u"x", "deviceModel": None, "protocol": u"http"}
def ok(m): res["ok"] = m; done.set()
def bad(m): res["err"] = m; done.set()
NET.call(lambda: cl.send(dev, [retrosend.make_file_item(big)], ok, bad))
for i in range(100):
    if seen["upload_started"]: break
    time.sleep(0.05)
time.sleep(0.3)
NET.call(cl.cancel)
done.wait(15)
time.sleep(0.5)
check("send cancelled with 'Cancelled.'", res.get("err") == u"Cancelled.")
check("receiver was told to cancel", seen["cancel"] == 1)

# ---- 2. stop receiving while a big upload arrives slowly
ev2 = Ev()
server = retrosend.Server(cfg, ev2, NET)
NET.call(server.start); time.sleep(0.3)
info = {"alias": "PC", "version": "2.0", "fingerprint": "pc", "port": 53999, "protocol": "http"}
c = httplib.HTTPConnection("127.0.0.1", 53410, timeout=10)
c.request("POST", "/api/localsend/v2/prepare-upload", json.dumps({"info": info, "files": {"a": {"id": "a", "fileName": "movie.mp4", "size": 3000000, "fileType": "video/mp4"}}}))
r = json.loads(c.getresponse().read())
up = socket.create_connection(("127.0.0.1", 53410))
up.sendall("POST /api/localsend/v2/upload?" + urllib.urlencode({"sessionId": r["sessionId"], "fileId": "a", "token": r["files"]["a"]}) +
           " HTTP/1.1\r\nContent-Length: 3000000\r\n\r\n")
up.sendall("x" * 200000); time.sleep(0.5)
check("partial file exists during receive", os.path.exists(cfg["savedir"] + "movie.mp4"))
check("receiving() is true", server.receiving())
NET.call(server.stop_receiving); time.sleep(0.5)
check("Receiving stopped reported", u"Receiving stopped." in ev2.fin)
check("partial file deleted", not os.path.exists(cfg["savedir"] + "movie.mp4"))
check("receiving() is false", not server.receiving())
print(fails and "FAILED" or "ALL PASSED")
sys.stdout.flush()
os._exit(len(fails))
