# Minimal LocalSend command line for the PC (Python 2.7), built on localsend22.
#   python2 examples/pc_cli.py receive [dir]            wait for files (auto-accept)
#   python2 examples/pc_cli.py send HOST FILE [FILE..]  send files to HOST (plain HTTP)
#   python2 examples/pc_cli.py text HOST "message"      send a text message
# The other LocalSend must have "Encryption" turned OFF (plain HTTP only).
import os, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import localsend22 as ls
import pcnet


class Events:
    def log(self, m): print("log: %s" % m)
    def peer(self, dev): print("peer: %s" % dev["alias"])
    def ask_accept(self, dev, files, total, answer):
        print("accepting %d file(s) from %s" % (len(files), dev["alias"])); answer(1)
    def text_received(self, dev, text): print("text from %s: %s" % (dev["alias"], text))
    def progress(self, t, d, total): print("%s %d/%d" % (t, d, total))
    def finished(self, m): print("finished: %s" % m)


def main():
    if len(sys.argv) < 2:
        print(__doc__); return 1
    cmd = sys.argv[1]
    net = pcnet.SelectNet()
    th = threading.Thread(target=net.run); th.setDaemon(True); th.start()
    cfg = {"alias": u"localsend22 CLI", "fingerprint": ls.random_id() + ls.random_id(),
           "port": ls.DEFAULT_PORT, "model": u"PC", "savedir": (sys.argv[2:3] or ["."])[0] + os.sep}
    ev = Events()
    if cmd == "receive":
        srv = ls.Server(cfg, ev, net)
        net.call(srv.start)
        net.call(lambda: ls.Client(cfg, ev, net).announce())
        while 1: time.sleep(1)
    host = sys.argv[2]
    box = {}
    def ok(m): print(m); box["done"] = 1
    def bad(m): print("error: %s" % m); box["done"] = 1
    def go(host=host):
        def registered(dev):
            if cmd == "text":
                items = [ls.make_text_item(sys.argv[3].decode("utf-8"))]
            else:
                items = [ls.make_file_item(p) for p in sys.argv[3:]]
            ls.Client(cfg, ev, net).send(dev, items, ok, bad)
        ls.Client(cfg, ev, net).register(host, ls.DEFAULT_PORT, registered, bad)
    net.call(go)
    while not box: time.sleep(0.2)
    return 0

sys.exit(main())
