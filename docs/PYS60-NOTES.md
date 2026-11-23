# Python for S60 1.4.5: pitfalls found while building RetroSend

These notes are for anyone writing networking code for **Python for S60 1.4.5** (Python 2.2.2 on Symbian OS 9 / S60 3rd Edition). Each was found on a real Nokia N79 and confirmed in the PyS60 `e32socket` source.

## 1. Use sockets only on the main thread, with callbacks

**Symptom:** the app freezes as soon as a background thread (`thread.start_new_thread`) does `socket()`, `bind()` or `accept()`.

**Why:** every blocking e32socket call waits on a `CActiveSchedulerWait`. Only the main (UI) thread has a Symbian active scheduler. In a secondary thread the call fails at the native level while the interpreter lock is held, so the whole app freezes.

**Fix:** keep everything on the main thread and use the callback forms:

```python
s.accept(cb)                 # cb((new_sock, (ip, port)))
s.recv(n, 0, cb)             # cb(data); "" at end of stream
s.send(data, 0, cb)          # cb(bytes_sent)
s.connect((host, port), cb)  # cb(None)
u.recvfrom(n, 0, cb)         # cb((data, (ip, port)))
```

On error, the callback gets `(exception_class, value)` instead. Check `type(res[0])` against `types.ClassType`.

Call these on the raw `e32socket` object (`sock._sock` behind the `socket` module wrapper) so the callback argument is passed through. Use `e32.Ao_timer().after(sec, cb)` for timers, and `e32.Ao_lock().wait()` as the main loop.

## 2. `socket.close()` stops Wi-Fi for every socket

`socket_close()` in e32socket also calls `RConnection::Stop()` on the default access point and closes the shared `RSocketServ`. After one `close()`, every other socket, including your listening socket, is broken, and the next call crashes the app.

**Fix:** don't call `close()` while the app runs. For a connected socket, call `shutdown(2)` and drop the reference. Freeing the object (`Socket_dealloc`) closes only that socket. Never close a socket that has a pending `connect`. Call `close()` only on exit.

## 3. `bind(("", port))` → `(22, 'Invalid argument')`

The address goes through `TInetAddr::Input()`, which rejects an empty string. Use `bind(("0.0.0.0", port))`.

## 4. No way to learn who connected

`accept()` builds the peer address from the first 4 bytes of a `TSockAddr`, which are the address family, not the IP. You always get `0.8.0.0`. `getpeername()` and `getsockname()` aren't implemented. (`recvfrom()` on UDP formats the address correctly.)

**Consequence:** a server can't learn a client's IP from TCP. A TCP sweep of the subnet doesn't work well either: e32socket opens the socket server with only 16 message slots, and each connect to an empty address holds one until the phone gives up (it can't be cancelled without `close()`, see 2). RetroSend instead uses the fact that LocalSend answers every UDP announce by registering over HTTP, and the registration carries its fingerprint. It announces to half of the remaining addresses (UDP, no connections), checks whether that fingerprint answered, and repeats. About 8 rounds find the device among 254 addresses, then one HTTP register confirms it.

## 5. The access point: ask once, then reuse

```python
apid = socket.select_access_point()     # shows the Wi-Fi list; store apid
apo = socket.access_point(apid)
socket.set_default_access_point(apo)    # no "select access point" pop-up after this
apo.start(); ip = apo.ip()              # blocking, 1-2 s
```

`apo.start()` is synchronous and blocks the UI for a second or two. In the Offline profile, the phone first asks "Create WLAN connection in offline mode?". The app can't suppress that.

## 6. Python 2.2 traps

- `random.randint(0, 0x7fffffff)` → `OverflowError: long int too large to convert to int`, because `randint` adds 1 to the upper bound. Use small ranges.
- There's no `bool` type: `True` is `1`, so a JSON encoder can't tell them apart. RetroSend uses explicit `JTRUE`/`JFALSE` markers.
- Python 2.2 also lacks `enumerate`, `sorted`, `set`, generator expressions, `x if c else y`, `with`, `try/except/finally` in one statement, `"ab" in s` for substrings (use `find()`), and `b''` literals.

## 7. Diagnosing crashes

A crash during startup just closes the app silently. RetroSend writes `E:\retrosend_log.txt` (appended to, with timestamps) and shows Python tracebacks on screen. A 2-second "tick" line in the log shows whether the UI is still alive. This makes freezes and crashes easy to tell apart.

## 8. Packaging (Ensymble on modern Linux)

Ensymble 0.29's `py2sis` needs Python 2 and calls OpenSSL. On current systems, two fixes are needed (see `tools/ensymble-retrosend.patch` in the RetroSend repository): pass OpenSSL arguments as a list, and use `communicate()` so OpenSSL doesn't wait forever on stdin. Run it with `</dev/null`, or it waits for a passphrase. Private keys must be in traditional `BEGIN RSA PRIVATE KEY` format (`openssl rsa -traditional`).

`ensymble mergesis app.sis PythonForS60.sis out.sis` embeds the Nokia-signed runtime. Its signature stays intact, so users install one file.
