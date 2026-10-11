"""
Plain-HTTP front door on port 80. Two jobs:

1. The PTC login endpoints for the 0.35 client only.

   Why: 0.35 dropped the `MyRemoteCertificateValidationCallback` that 0.29's
   PTC login class installed. Without it, the Mono/C# login path validates
   server certs against Unity 5.3.5's bundled 2016-era root store, which trusts
   neither our private CA nor a modern Let's Encrypt chain (today's LE certs
   chain to ISRG Root X1, which that store predates). Measured symptom, from
   the client's own log:

       System.Net.WebException: Error getting response stream
           (Write: BeginWrite failure): SendFailure
       Authentication failed state: Initial PTC login failed.

   Since no certificate can satisfy that store, take TLS out of the login hop
   entirely: the 0.35 APK's five SSO URLs are rewritten to http:// and this
   bridge forwards them to the real HTTPS server on loopback. Only PTC login
   crosses it -- the RPC path stays HTTPS on the native stack, which has its
   own (working) trust. On a tailnet the hop is WireGuard-encrypted anyway.

2. The phone setup page, `http://<server ip>/`, with the CA at `/ca.crt`.

   This is the one thing a phone can fetch from us BEFORE it trusts our
   certificate, so it is where the certificate comes from: open the page in
   the phone's browser, tap the download, install. No AirDrop, no email. The
   page also reads the Host header to tell the player whether their DNS
   already points here. The CA certificate is public by nature; the keys
   never leave the certs/ folder.

Everything else that finds port 80 (Tailscale probes for /ts2021, scanners)
is refused rather than quietly proxied into the game server.

    import sso_bridge; sso_bridge.serve()        # or run this file directly
"""
import html
import http.client
import os
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from windstock.config import paths

_CTX = ssl._create_unverified_context()          # loopback hop; our own cert
UPSTREAM = 443                                   # set by serve()

# Our hostnames: a request that arrives under one of these came through the
# DNS redirect, which is exactly what the setup page wants to confirm.
OUR_DOMAINS = ("nianticlabs.com", "pokemon.com", "zendesk.com")


def _ca_path():
    return os.path.join(os.environ.get("CERT_DIR", paths.CERT_DIR), "ca.crt")


def _server_ip(host):
    ip = os.environ.get("REDIRECT_IP", "")
    if ip and ip != "0.0.0.0":
        return ip
    return host or "this computer's IP"


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Windstock setup</title>
<style>
 body{margin:0;padding:22px 18px 40px;font:17px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif;
      color:#172236;background:#eaeef6;-webkit-text-size-adjust:100%}
 h1{font-size:24px;margin:0 0 4px} .sub{color:#586074;margin:0 0 18px}
 .card{background:#fff;border:1px solid #e4e9f2;border-radius:14px;padding:16px 18px;margin:0 0 14px}
 .ok{color:#127a45;font-weight:700} .todo{color:#b45309;font-weight:700}
 .n{display:inline-block;width:26px;height:26px;border-radius:50%;background:#2358d8;color:#fff;
    text-align:center;line-height:26px;font-weight:800;margin-right:8px}
 .btn{display:block;text-align:center;background:#2358d8;color:#fff;text-decoration:none;
      font-weight:800;padding:14px;border-radius:999px;margin:12px 0}
 code{background:#f3f6fc;padding:2px 6px;border-radius:6px;font-size:15px}
 ol{padding-left:22px;margin:8px 0} li{margin:4px 0} small{color:#586074}
</style></head><body>
<h1>Windstock</h1>
<p class="sub ok">&#10003; Your phone can reach the server.</p>

<div class="card"><span class="n">1</span><b>Install the certificate</b>
<a class="btn" href="/ca.crt">Download certificate</a>
<b>iPhone:</b> tap <i>Allow</i>, then
<ol><li>Settings &rarr; General &rarr; VPN &amp; Device Management &rarr; <i>PoGO Private CA</i> &rarr; Install</li>
<li>Settings &rarr; General &rarr; About &rarr; Certificate Trust Settings &rarr; turn on <i>PoGO Private CA</i></li></ol>
<small>Step 2 is the one people miss: without it the game says <i>Unable to authenticate</i>.</small><br>
<b>Android:</b> Settings &rarr; Security &rarr; Encryption &amp; credentials &rarr; Install a certificate &rarr;
CA certificate &rarr; pick the downloaded file.</div>

<div class="card"><span class="n">2</span><b>Point DNS at the server</b><br>
__DNS__</div>

<div class="card"><span class="n">3</span><b>Check, then play</b><br>
Open <a href="https://pgorelease.nianticlabs.com/check">https://pgorelease.nianticlabs.com/check</a>.
A green tick there means DNS and the certificate both work.<br>
Then open Pok&eacute;mon GO &rarr; <b>Pok&eacute;mon Trainer Club</b> &rarr; any username and password.
<small>Your first login sets that trainer's password, so keep using the same one.</small></div>
</body></html>
"""

_DNS_OK = ('<span class="ok">&#10003; Done &mdash; this page came through the '
           'Windstock DNS.</span>')
_DNS_TODO = ('<span class="todo">Not yet.</span> Wi-Fi &rarr; your network &rarr; '
             '<i>Configure DNS</i> &rarr; <b>Manual</b>: remove every server and add only '
             '<code>__IP__</code>. (Android: Wi-Fi &rarr; Modify &rarr; Static &rarr; DNS 1.) '
             'Then open <a href="http://pgorelease.nianticlabs.com/">'
             'http://pgorelease.nianticlabs.com/</a> &mdash; this page should load '
             'again with a tick here.')


def setup_page(host):
    host = (host or "").split(":")[0].lower()
    dns_ok = any(host == d or host.endswith("." + d) for d in OUR_DOMAINS)
    dns = _DNS_OK if dns_ok else _DNS_TODO.replace("__IP__", html.escape(_server_ip(host)))
    return _PAGE.replace("__DNS__", dns).encode("utf-8")


class Bridge(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"[bridge] {self.command} {self.path}", flush=True)

    def _send(self, status, ctype, body, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle(self):
        path = self.path.partition("?")[0]
        if path in ("/", "/setup", "/index.html"):
            return self._send(200, "text/html; charset=utf-8",
                              setup_page(self.headers.get("Host")))
        if path == "/ca.crt":
            try:
                with open(_ca_path(), "rb") as fh:
                    pem = fh.read()
            except OSError:
                return self._send(404, "text/plain", b"no certificate yet")
            # The type iOS recognises as a certificate to install; the
            # filename is what the Files app / Android download shows.
            return self._send(200, "application/x-x509-ca-cert", pem,
                              {"Content-Disposition":
                               'attachment; filename="windstock-ca.crt"'})
        if path.startswith("/sso"):
            return self._relay()
        self._send(404, "text/plain", b"")

    def _relay(self):
        body = b""
        n = self.headers.get("Content-Length")
        if n:
            body = self.rfile.read(int(n))
        c = http.client.HTTPSConnection("127.0.0.1", UPSTREAM, context=_CTX, timeout=30)
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("content-length", "accept-encoding")}
        # Keep the Host the client sent; server.py routes /sso by path, but the
        # PTC handler echoes this host back into the URLs it hands the client,
        # so rewriting it here would send the client somewhere it cannot reach.
        try:
            c.request(self.command, self.path, body=body, headers=headers)
            r = c.getresponse()
            data = r.read()
        except Exception as e:
            print(f"[bridge] upstream error: {e}", flush=True)
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() in ("transfer-encoding", "content-length", "connection"):
                continue
            # Redirects point back at https://<host>/sso/... -- the 0.35 client
            # cannot do TLS to us at all, so keep the whole login flow on http.
            if k.lower() == "location":
                v = v.replace("https://", "http://")
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_HEAD = _handle


def serve(listen=80, upstream=443):
    """Blocking. Run me in a daemon thread next to the DNS redirector."""
    global UPSTREAM
    UPSTREAM = upstream
    print(f"[bridge] http://0.0.0.0:{listen}  ->  https://127.0.0.1:{upstream}"
          f"  (phone setup page + /ca.crt)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", listen), Bridge).serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 80,
          int(sys.argv[2]) if len(sys.argv) > 2 else 443)
