# localsend-py22

A small, dependency-free **LocalSend protocol v2** client and server for **Python 2.2**, written for **Python for S60 1.4.5** on old Nokia phones (S60 3rd Edition), and also running on Python 2.7 on a PC.

It is the protocol core of [RetroSend](https://github.com/manojanasuri16/RetroSend), extracted into a standalone library with its own tests and a command line example. The code is transport-independent: you give it a "net" object (sockets and timers), so the same code runs on PyS60's callback sockets (`s60net.py`) and on a PC `select()` loop (`pcnet.py`).

## Status and what was verified

| What | Status |
|---|---|
| Protocol logic (`localsend22.py`) on Python 2.7 against fake peers | **Tested** (`tests/`, results below) |
| Python 2.2 syntax compliance | **Checked** with `tools/check_py22.py` (rejects constructs newer than 2.2) |
| The same code inside RetroSend on a real **Nokia N79** (S60 3rd FP2, PyS60 1.4.5) talking to the LocalSend app | **Used in RetroSend on the N79** (see the RetroSend README). This repository's extracted copy has **not been re-run on a phone**; the PyS60 backend `s60net.py` is the unchanged RetroSend code |
| Other S60 3rd Edition phones (N82, N95, E71, ...) | **Untested** |
| S60 5th Edition / Symbian^3 with PyS60 2.x | **Untested** (the PyS60 1.4.5 callback socket API is assumed) |
| TLS / HTTPS | **Not supported** (see Limitations) |

Test run on the PC (Python 2.7.15): `tests/test_protocol.py` ALL PASSED, `tests/test_cancel.py` ALL PASSED, `check_py22.py` OK. An end-to-end send of a file and of a text message between two instances of `examples/pc_cli.py` over loopback also worked.

## Features

- LocalSend v2 endpoints, server side: `info`, `register`, `prepare-upload` (with an accept/decline callback), `upload` (streams to disk with token and session checks, 409 when busy, safe file names), `cancel`.
- Client side: `register`, `prepare-upload`, `upload` with progress, cancel (the receiver is told), text messages (a `text/plain` file with a `preview` field), 204 "nothing to send" handling, friendly messages for 401 (PIN), 403 (declined) and 409 (busy).
- UDP multicast announce (`224.0.0.167:53317`, plus a subnet broadcast).
- A tolerant HTTP/1.x request parser and body decoder (content-length and chunked) that works on a non-blocking, callback-driven socket.
- A tiny JSON encoder/decoder (Python 2.2 has no `json` module, and no `bool`).
- Pure Python, one file for the protocol (`localsend22.py`, about 1,150 lines), no third-party packages.

## Layout

```
localsend22.py     protocol core: JSON, HTTP, Server, Client, helpers (Python 2.2 safe)
s60net.py          PyS60 1.4.5 net backend (callback sockets, Ao_timer); phone only
pcnet.py           PC net backend (select loop, Python 2.7); used by tests and the example
examples/pc_cli.py  receive / send / text command line for the PC
tests/             protocol tests against fake LocalSend peers, cancel tests
tools/check_py22.py  rejects syntax newer than Python 2.2
docs/PYS60-NOTES.md  PyS60 1.4.5 socket pitfalls found on a real N79
```

## How it fits together

`Server(cfg, events, net)` and `Client(cfg, events, net)` do the protocol. They call:

- **net** (you provide, or use `s60net.PhoneNet` / `pcnet.SelectNet`): `listen(port, on_conn)`, `connect(host, port, on_ok, on_err)`, `later(seconds, func)`, `udp_send(host, port, data)`, `udp_listen(port, on_packet)`. A connection has `recv(on_data)`, `send(data, on_done, on_err)`, `close()`, `local_ip()`.
- **events** (you provide): `log(msg)`, `peer(dev)`, `ask_accept(dev, files, total, answer)` (call `answer(1)` or `answer(0)`), `text_received(dev, text)`, `progress(title, done, total)`, `finished(msg)`.
- **cfg**: a dict with `alias`, `fingerprint` (any random string; see Limitations), `port`, `savedir` (a folder path ending in a separator) and optionally `model`, `sort`.

### Send a file (PC, Python 2.7)

```python
import threading, localsend22 as ls, pcnet

net = pcnet.SelectNet()
t = threading.Thread(target=net.run); t.setDaemon(True); t.start()
cfg = {"alias": u"me", "fingerprint": ls.random_id(), "port": 53317, "model": u"PC"}

class Ev:                       # only what the client needs
    def log(self, m): print(m)
    def progress(self, t, d, total): print("%s %d/%d" % (t, d, total))
    def peer(self, dev): pass

def registered(dev):
    ls.Client(cfg, Ev(), net).send(dev, [ls.make_file_item("photo.jpg")],
                                   lambda m: None, lambda e: None)

net.call(lambda: ls.Client(cfg, Ev(), net).register("192.168.1.20", 53317, registered, lambda e: None))
```

### Receive

```python
srv = ls.Server(dict(cfg, savedir="/tmp/in/"), events, net)
net.call(srv.start)
```

### On the phone

```python
import s60net, localsend22 as ls
class App:                       # what s60net needs
    def safe(self, f, *a): apply(f, a)
    def log(self, m): pass
app = App(); app.net = s60net.PhoneNet(app)
ls.Server(cfg, app, app.net).start()   # app also implements the events above
```

Run everything on the main thread, driven by `e32.Ao_lock().wait()`. See `docs/PYS60-NOTES.md` for why (no threads, never `close()` a socket, `bind("0.0.0.0")`, wrong `accept()` peer address).

## Run the examples and tests

You need Python 2.7 on the PC (the tests use `BaseHTTPServer` and `httplib`) and Python 3 for the syntax check.

```sh
python3 tools/check_py22.py localsend22.py s60net.py
python2 tests/test_protocol.py
python2 tests/test_cancel.py

python2 examples/pc_cli.py receive ./incoming
python2 examples/pc_cli.py send 192.168.1.20 photo.jpg
python2 examples/pc_cli.py text 192.168.1.20 "hello"
```

## Limitations (read these)

- **Plain HTTP only.** The other LocalSend must have **Encryption turned off** in Settings. HTTPS needs TLS 1.2 with modern ciphers and self-signed certificates; Python 2.2 has no usable `ssl` module and PyS60 1.4.5's socket layer offers none. Nothing here implements TLS, certificate generation, or LocalSend's certificate-fingerprint identity: the `fingerprint` is just a random identifier. Do not use this on networks you do not trust.
- No PIN support (a receiver that requires one answers 401, which is reported). No `download`/browser-share API in this library (RetroSend's browser sharing is app code and is not included).
- Discovery: this library announces by UDP multicast and answers `register`. Finding the IP of a device that registers with you is **not** possible from TCP on PyS60 1.4.5 (`accept()` returns a bogus peer address). RetroSend works around it with a UDP "halving search" in its app class; that code is not extracted here (it is described in `docs/PYS60-NOTES.md` section 4).
- Only one transfer session at a time (a second `prepare-upload` gets 409).
- Written against LocalSend protocol v2.0/2.1 as observed from the LocalSend app in 2026; later protocol changes are not tracked. Not affiliated with LocalSend.

## Related

- [RetroSend](https://github.com/manojanasuri16/RetroSend): the full N79 app built on this code.
- [symbian-s60v3-toolchain](https://github.com/manojanasuri16/symbian-s60v3-toolchain): Symbian C++ toolchain for S60 3rd FP2 on modern Linux (not needed for this pure-Python library).

## Licence

MIT, see `LICENSE`.

## Credits and third-party notices

- [LocalSend](https://localsend.org) and its [protocol](https://github.com/localsend/protocol) (Apache-2.0). This is an independent implementation.
- [Python for S60](https://sourceforge.net/projects/pys60/) by Nokia (the runtime this targets; not included).

## Part of the RetroArchive ecosystem

Extracted from [RetroSend](https://github.com/manojanasuri16/RetroSend). All apps, guides and open-source repos are listed on [RetroArchive](https://manojanasuri16.github.io/RetroArchive/) (see the [Open source page](https://manojanasuri16.github.io/RetroArchive/opensource/)); the build toolchain is [symbian-s60v3-toolchain](https://github.com/manojanasuri16/symbian-s60v3-toolchain).
