"""
Stand-in for the customer's MES during a demo.

  POST /api/upload   receives a file from CNCBridge (request body or multipart form), checks the token
  GET  /             inbox page listing everything received

Two switches, flipped from the Demo control window:
  online = False     every upload is answered 503, as when the customer's server is down
  refuse_next = True the next upload is answered 422, as when the customer rejects one file
"""
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Customer MES inbox (demo)</title><style>
:root{--bg:#f1f4f7;--card:#fff;--ink:#18222e;--mu:#566475;--line:#bcc6d2;--ok:#1e7a45;--okbg:#dcefe3;--bad:#ad2f1f;--badbg:#f8e1dc}
@media (prefers-color-scheme:dark){:root{--bg:#10151b;--card:#1a212a;--ink:#e4eaf1;--mu:#9aa7b6;--line:#3c4857;--ok:#7fd3a0;--okbg:#173524;--bad:#ff9a8a;--badbg:#43201a}}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 "Segoe UI",system-ui,sans-serif}
header{padding:14px 22px;background:var(--card);border-bottom:1px solid var(--line);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
h1{font-size:18px;margin:0}.mu{color:var(--mu)}main{max-width:1150px;margin:0 auto;padding:16px 22px}
.st{padding:4px 12px;border-radius:99px;font-weight:600;font-size:12.5px;background:var(--okbg);color:var(--ok)}.st.down{background:var(--badbg);color:var(--bad)}
.nums{display:flex;gap:34px;flex-wrap:wrap;margin-bottom:14px}.big{font-size:28px;font-weight:700}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.5fr);gap:14px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:12px}
.it{padding:8px 10px;border-bottom:1px solid var(--line);cursor:pointer;font-size:13px}.it:hover,.it.sel{background:var(--bg)}
.it b{font-family:Consolas,monospace;font-size:12.5px;word-break:break-all}
pre{margin:0;font:12px/1.5 Consolas,monospace;white-space:pre;overflow:auto;max-height:640px}</style></head><body>
<header><h1>Customer MES inbox</h1><span class="mu">stand-in receiver for the CNCBridge demo</span><span style="flex:1"></span><span id="st" class="st">Online</span></header>
<main><div class="card nums"><div><div class="big" id="n">0</div><span class="mu">files received</span></div>
<div><div class="big" id="un">0</div><span class="mu">turned away during outage</span></div>
<div><div class="big" id="rf">0</div><span class="mu">refused</span></div>
<div><div class="big" id="last">-</div><span class="mu">last received</span></div></div>
<div class="grid"><div class="card"><div id="list" class="mu">Waiting for CNCBridge...</div></div><div class="card"><pre id="body">Select a file</pre></div></div></main>
<script>
let sel=null;const $=id=>document.getElementById(id);
async function poll(){let s;try{s=await (await fetch('/api/received')).json()}catch(e){return}
 $('n').textContent=s.items.length;$('un').textContent=s.unavailable;$('rf').textContent=s.refused;
 $('st').textContent=s.online?'Online':'OUTAGE (answers 503)';$('st').className='st'+(s.online?'':' down');
 $('last').textContent=s.items.length?s.items[s.items.length-1].time:'-';
 if(!s.items.length){$('list').textContent='Waiting for CNCBridge...';$('body').textContent='Select a file';sel=null;return}
 if(sel===null||sel>=s.items.length){sel=s.items.length-1;show()}
 $('list').innerHTML='';for(const i of s.items.slice().reverse()){const d=document.createElement('div');d.className='it'+(sel===i.id?' sel':'');
  const b=document.createElement('b');b.textContent=i.name;const m=document.createElement('div');m.className='mu';m.textContent=i.time+' | '+i.type+' | '+i.size+' bytes';
  d.append(b,m);d.onclick=()=>{sel=i.id;show();poll()};$('list').append(d)}}
async function show(){$('body').textContent=await (await fetch('/api/file/'+sel)).text()}
poll();setInterval(poll,1500);</script></body></html>"""


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows "reuse" would let two programs share the port silently
    daemon_threads = True


def _from_multipart(body, ctype):
    boundary = ctype.split("boundary=")[-1].encode()
    for part in body.split(b"--" + boundary):
        if b'name="file"' in part:
            head, _, data = part.partition(b"\r\n\r\n")
            return head.split(b'filename="')[1].split(b'"')[0].decode(), data[:-2]
    return "upload.bin", body


class MockMES:
    def __init__(self, store, token="", port=8765):
        self.store, self.token = Path(store), token
        self.store.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.items, self.online, self.refuse_next, self.refused, self.unavailable = [], True, False, 0, 0
        for candidate in range(port, port + 20):
            try:
                self.server = _Server(("127.0.0.1", candidate), self._handler())
                break
            except OSError:
                continue
        else:
            raise OSError(f"no free port between {port} and {port + 19}")
        self.port = self.server.server_address[1]
        self.page = f"http://127.0.0.1:{self.port}/"
        self.url = self.page + "api/upload"

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def clear(self):
        with self.lock:
            self.items, self.refused, self.unavailable, self.refuse_next = [], 0, 0, False
            for f in self.store.glob("*"):
                f.unlink()

    def _handler(mes):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data, ctype="application/json"):
                data = data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/":
                    return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                if self.path == "/api/received":
                    with mes.lock:
                        return self._send(200, {
                            "online": mes.online, "refused": mes.refused, "unavailable": mes.unavailable,
                            "items": [{k: v for k, v in i.items() if k != "path"} for i in mes.items]})
                if self.path.startswith("/api/file/"):
                    try:
                        item = mes.items[int(self.path.rsplit("/", 1)[1])]
                    except (ValueError, IndexError):
                        return self._send(404, {"error": "not found"})
                    return self._send(200, Path(item["path"]).read_bytes(), "text/plain; charset=utf-8")
                self._send(404, {"error": "not found"})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path != "/api/upload":
                    return self._send(404, {"error": "not found"})
                with mes.lock:
                    if not mes.online:
                        mes.unavailable += 1
                        return self._send(503, {"error": "MES temporarily unavailable"})
                    if mes.token and self.headers.get("Authorization", "") != f"Bearer {mes.token}":
                        return self._send(401, {"error": "wrong token"})
                    if mes.refuse_next:
                        mes.refuse_next = False
                        mes.refused += 1
                        return self._send(422, {"error": "this file was refused by the MES (demo)"})
                    ctype = self.headers.get("Content-Type", "")
                    name = self.headers.get("X-Filename", "upload.bin")
                    if ctype.startswith("multipart/form-data"):
                        name, body = _from_multipart(body, ctype)
                    number = len(mes.items)
                    path = mes.store / f"{number:04d}_{Path(name).name}"
                    path.write_bytes(body)
                    mes.items.append({"id": number, "name": name, "type": ctype.split(";")[0], "size": len(body),
                                      "time": datetime.now().strftime("%H:%M:%S"), "path": str(path)})
                self._send(201, {"status": "accepted", "id": number})
        return H
