"""Serve one disposable Git repository using Git's read-only smart HTTP backend."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys


class UploadPack(BaseHTTPRequestHandler):
    timeout = 10

    def do_GET(self):
        if self.path == "/lab.git/info/refs?service=git-receive-pack":
            self.send_error(403)
            return
        if self.path != "/lab.git/info/refs?service=git-upload-pack":
            self.send_error(404)
            return
        self.serve_git("/lab.git/info/refs", "service=git-upload-pack", b"")

    def do_POST(self):
        if self.path != "/lab.git/git-upload-pack":
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = 0
        if not 0 < length <= 1024 * 1024 or self.headers.get("Transfer-Encoding"):
            self.send_error(400)
            return
        if self.headers.get_content_type() != "application/x-git-upload-pack-request":
            self.send_error(415)
            return
        content = self.rfile.read(length)
        if len(content) != length:
            self.send_error(400)
            return
        self.serve_git(self.path, "", content)

    def serve_git(self, path, query, content):
        protocol = self.headers.get("Git-Protocol", "")
        if protocol not in {"", "version=1", "version=2"}:
            self.send_error(400)
            return
        environment = {
            "PATH": "/usr/bin:/bin", "GIT_PROJECT_ROOT": self.server.repository_root,
            "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": path, "QUERY_STRING": query,
            "REQUEST_METHOD": self.command, "CONTENT_LENGTH": str(len(content)),
            "CONTENT_TYPE": self.headers.get("Content-Type", ""), "HTTP_GIT_PROTOCOL": protocol,
        }
        try:
            result = subprocess.run(
                ("/usr/bin/git", "-c", "http.receivepack=false", "http-backend"),
                env=environment, input=content, capture_output=True, timeout=10, check=True,
            )
        except (OSError, subprocess.SubprocessError):
            self.send_error(502)
            return
        headers, delimiter, body = result.stdout.partition(b"\r\n\r\n")
        if not delimiter:
            self.send_error(502)
            return
        fields = dict(line.decode("ascii").split(": ", 1) for line in headers.split(b"\r\n"))
        self.send_response(int(fields.pop("Status", "200").split()[0]))
        for key in ("Content-Type", "Cache-Control", "Expires", "Pragma"):
            if key in fields:
                self.send_header(key, fields[key])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    address, port, root = sys.argv[1:]
    with ThreadingHTTPServer((address, int(port)), UploadPack) as server:
        server.repository_root = root
        server.serve_forever()
