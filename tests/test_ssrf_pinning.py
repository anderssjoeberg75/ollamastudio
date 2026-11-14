"""Integration test for the SSRF pin-to-IP connections in studio/websearch.py.

A local HTTPS server presents a self-signed certificate that is ONLY valid for
the hostname "pinned.test". The client resolves that hostname (stubbed) to
127.0.0.1 and connects there. The test proves the two properties pinning needs:

* the socket goes to the pinned IP, while
* SNI and certificate verification use the hostname, not the IP
  (a different hostname pinned to the same IP must fail verification).

Requires the `openssl` CLI to create the throw-away certificate; skipped otherwise.
"""
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import studio.websearch as ws

HOST = "pinned.test"
BODY = b"<html><body><p>pinned-ok</p></body></html>"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args):
        pass


@unittest.skipUnless(shutil.which("openssl"), "openssl CLI not available")
class TestPinnedHTTPS(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.cert = os.path.join(cls.tmp, "cert.pem")
        key = os.path.join(cls.tmp, "key.pem")
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=" + HOST, "-addext", "subjectAltName=DNS:" + HOST,
             "-keyout", key, "-out", cls.cert],
            check=True, capture_output=True)
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(cls.cert, key)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.srv.socket = server_ctx.wrap_socket(cls.srv.socket, server_side=True)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever,
                         kwargs={"poll_interval": 0.02}, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self._orig = ws._resolve_public_ip
        # Stubbad DNS: båda namnen pekar på den lokala servern.
        ws._resolve_public_ip = lambda host: "127.0.0.1"
        self.ctx = ssl.create_default_context(cafile=self.cert)

    def tearDown(self):
        ws._resolve_public_ip = self._orig

    def test_connects_to_pinned_ip_and_verifies_hostname(self):
        conn = ws._PinnedHTTPSConnection(HOST, self.port, context=self.ctx, timeout=5)
        try:
            conn.request("GET", "/")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertIn(b"pinned-ok", resp.read())
        finally:
            conn.close()

    def test_wrong_hostname_fails_certificate_check(self):
        # Samma fästa IP, men certet gäller inte för "other.test" -> verifieringen
        # måste misslyckas. Bevisar att SNI/cert använder värdnamnet, inte IP:n.
        conn = ws._PinnedHTTPSConnection("other.test", self.port, context=self.ctx, timeout=5)
        try:
            with self.assertRaises(ssl.SSLCertVerificationError):
                conn.request("GET", "/")
        finally:
            conn.close()

    def test_full_urllib_path_through_pinned_handler(self):
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            ws._PinnedHTTPSHandler(context=self.ctx), ws._SafeRedirect)
        with opener.open("https://%s:%d/" % (HOST, self.port), timeout=5) as resp:
            self.assertIn(b"pinned-ok", resp.read())

    def test_private_target_is_refused_before_connecting(self):
        ws._resolve_public_ip = self._orig          # riktig validering igen
        conn = ws._PinnedHTTPSConnection("localhost", self.port, context=self.ctx, timeout=5)
        try:
            with self.assertRaises(ValueError):    # localhost -> loopback -> nekas
                conn.request("GET", "/")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
