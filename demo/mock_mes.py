"""Local HTTP receiver used by the integrated demo and its delivery tests."""
import json
import threading
import uuid
from datetime import datetime
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import urlparse


class ExclusiveHTTPServer(ThreadingHTTPServer):
    # HTTPServer's reuse flags allow two listeners on one port on some OS/Python
    # versions. Demo instances must never accept each other's customer payloads.
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        # The receiver is loopback-only. HTTPServer's reverse-DNS lookup can
        # otherwise block startup for tens of seconds on macOS.
        TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]

PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CNCBridge demo MES</title><style>
:root{color-scheme:light dark;font:16px/1.5 system-ui}body{max-width:1100px;margin:24px auto;padding:0 20px}
button{font:inherit;padding:8px 16px;cursor:pointer}header{display:flex;gap:20px;align-items:center;flex-wrap:wrap}
main{display:grid;grid-template-columns:1fr 1.5fr;gap:18px}pre{overflow:auto;padding:16px;background:#8882}
.item{display:block;text-align:left;width:100%;margin:8px 0;overflow-wrap:anywhere}#status{font-weight:bold}
@media(max-width:700px){main{grid-template-columns:1fr}}</style>
<header><h1>Demo customer MES</h1><span id="status">Connecting…</span><button id="outage">Simulate outage</button></header>
<p>This receiver accepts real HTTP uploads from the CNCBridge desktop app using invented machine data.</p>
<p id="counts"></p><main><div id="list"></div><pre id="body">Select an accepted file to inspect its exact contents.</pre></main>
<script>
const el=id=>document.getElementById(id);let selected=null;
async function poll(){try{
 const response=await fetch('/api/received');const s=await response.json();
 el('status').textContent=s.online?'Online':'Outage (HTTP 503)';
 el('outage').textContent=s.online?'Simulate outage':'Restore service';
 el('counts').textContent=s.items.length+' accepted · '+s.unavailable+' unavailable · '+s.refused+' refused';
 el('list').replaceChildren();
 for(const item of s.items.slice().reverse()){
   const button=document.createElement('button');button.className='item';
   button.textContent=item.name+' · '+item.size+' bytes · '+item.time;
   button.onclick=async()=>{selected=item.id;el('body').textContent=await(await fetch('/api/file/'+item.id)).text()};
   el('list').append(button);
 }
 }catch{el('status').textContent='Receiver unavailable'}}
el('outage').onclick=async()=>{await fetch('/api/outage',{method:'POST'});poll()};
poll();setInterval(poll,1500);
</script></html>"""


class MockMES:
    def __init__(self, store, token="demo-token", port=8080):
        self.store = Path(store)
        self.store.mkdir(parents=True, exist_ok=True)
        self.token, self.online, self.refuse_next = token, True, False
        self.items, self.unavailable, self.refused = [], 0, 0
        self.lock = threading.Lock()
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, code, value, content_type="application/json"):
                data = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                route = urlparse(self.path).path
                if route == "/":
                    return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
                if route == "/api/received":
                    with receiver.lock:
                        return self.send(200, {
                            "online": receiver.online, "unavailable": receiver.unavailable,
                            "refused": receiver.refused,
                            "items": [{k: v for k, v in item.items() if k != "path"}
                                      for item in receiver.items],
                        })
                if route.startswith("/api/file/"):
                    try:
                        index = int(route.rsplit("/", 1)[1])
                        with receiver.lock:
                            if index < 0:
                                raise IndexError()
                            stored = Path(receiver.items[index]["path"])
                        return self.send(200, stored.read_bytes(), "text/plain; charset=utf-8")
                    except (IndexError, ValueError):
                        pass
                self.send(404, {"error": "not found"})

            def do_POST(self):
                route = urlparse(self.path).path
                if route == "/api/outage":
                    with receiver.lock:
                        receiver.online = not receiver.online
                        return self.send(200, {"online": receiver.online})
                if route != "/api/upload":
                    return self.send(404, {"error": "not found"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self.send(400, {"error": "invalid Content-Length"})
                if not 0 <= length <= 20 * 1024 * 1024:
                    return self.send(413, {"error": "demo limit is 20 MB"})
                body = self.rfile.read(length)
                if receiver.token and self.headers.get("Authorization") != f"Bearer {receiver.token}":
                    return self.send(401, {"error": "incorrect demo token"})
                with receiver.lock:
                    if not receiver.online:
                        receiver.unavailable += 1
                        return self.send(503, {"error": "simulated outage"})
                    if receiver.refuse_next:
                        receiver.refuse_next = False
                        receiver.refused += 1
                        return self.send(422, {"error": "simulated file refusal"})
                content_type = self.headers.get("Content-Type", "")
                name = self.headers.get("X-Filename", "upload.bin")
                if content_type.startswith("multipart/form-data"):
                    message = BytesParser(policy=default).parsebytes(
                        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
                    file_part = next((p for p in message.iter_parts()
                                      if p.get_param("name", header="content-disposition") == "file"), None)
                    if file_part is None:
                        return self.send(400, {"error": "multipart file field missing"})
                    name, body = file_part.get_filename() or name, file_part.get_payload(decode=True)
                name = Path(name.replace("\\", "/")).name or "upload.bin"
                with receiver.lock:
                    index = len(receiver.items)
                    stored = receiver.store / f"{index:05d}_{uuid.uuid4().hex}_{name}"
                    stored.write_bytes(body)
                    receiver.items.append({
                        "id": index, "name": name, "path": str(stored), "size": len(body),
                        "type": content_type, "time": datetime.now().isoformat(timespec="seconds"),
                    })
                self.send(201, {"status": "accepted", "id": index, "filename": name})

        # Prefer the requested port; allow more than one isolated demo on the same PC.
        for candidate in ([port + i for i in range(20)] if port else [0]):
            try:
                self.server = ExclusiveHTTPServer(("127.0.0.1", candidate), Handler)
                break
            except OSError as exc:
                if exc.errno not in (48, 98, 10048):  # address already in use
                    raise
        else:
            raise OSError("No free local port for the demo MES")
        self.port = self.server.server_address[1]
        self.page = f"http://127.0.0.1:{self.port}/"
        self.url = self.page + "api/upload"
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def close(self):
        if self.thread and self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(timeout=3)
        self.server.server_close()
