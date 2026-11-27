"""Um Confluence de mentira (servidor HTTP local) para testar o sync inteiro, sem rede e sem conta.

Responde so ao que o roleroster pergunta: a verificacao de credenciais, as pastas de um pai e as buscas
CQL de paginas (por ancestral, sem corpo, e por id, com corpo). Token errado leva 403, como no Confluence Cloud.
"""
import base64
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def code_macro(body):
    return (
        '<ac:structured-macro ac:name="code" ac:schema-version="1">'
        f"<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body></ac:structured-macro>"
    )


def section(title, body):
    """Uma pagina de cliente: titulo da secao e o bloco de perfis num macro de codigo."""
    return (
        '<ac:structured-macro ac:name="expand">'
        f'<ac:parameter ac:name="title">{title}</ac:parameter>'
        f"<ac:rich-text-body>{code_macro(body)}</ac:rich-text-body></ac:structured-macro>"
    )


def profile_block(name, account, role="dreamsquad-login", region="us-east-1"):
    return f"[{name}]\naws_account_id={account}\nrole_name={role}\nregion={region}\n"


class FakeConfluence:
    """folders: {pai: [(id, titulo)]}; pages: lista de dicts com id, title, squad, ancestors (titulos), body."""

    def __init__(self, email="dev@acme.com", token="tok", folders=None, pages=None, page_size=100):
        self.email, self.token = email, token
        self.folders, self.pages, self.page_size = folders or {}, pages or [], page_size
        self.requests = []
        self.server = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.requests.append(self.path)
                expected = base64.b64encode(f"{fake.email}:{fake.token}".encode()).decode()
                if self.headers.get("Authorization") != f"Basic {expected}":
                    return self.reply(403, {"message": "forbidden"})
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/wiki/rest/api/user/current":
                    return self.reply(200, {"accountId": "abc"})
                if parsed.path == "/wiki/rest/api/content/search":
                    return self.reply(200, fake.search(urllib.parse.parse_qs(parsed.query)))
                self.reply(404, {"message": "nao existe"})

            def reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    # ---- as buscas CQL que o roleroster faz

    def search(self, query):
        cql, expand = query["cql"][0], query.get("expand", [""])[0]
        start = int(query.get("start", ["0"])[0])
        if match := re.fullmatch(r"parent = (\d+) and type = folder", cql):
            rows = [{"id": i, "title": t} for i, t in self.folders.get(match.group(1), [])]
        elif match := re.fullmatch(r"type = page and ancestor in \(([\d, ]+)\)", cql):
            squads = {s.strip() for s in match.group(1).split(",")}
            rows = [
                {"id": p["id"], "title": p["title"], "ancestors": [{"title": t} for t in p["ancestors"]],
                 "version": {"number": p.get("version", 1)}}
                for p in self.pages if p["squad"] in squads
            ]
        elif match := re.fullmatch(r"type = page and id in \(([\d, ]+)\)", cql):
            ids = {i.strip() for i in match.group(1).split(",")}
            rows = [{"id": p["id"], "body": {"storage": {"value": p["body"]}}} for p in self.pages if p["id"] in ids]
        else:
            rows = []
        page = rows[start:start + self.page_size]
        payload = {"results": page}
        if start + self.page_size < len(rows):
            more = urllib.parse.urlencode({"cql": cql, "limit": 100, "expand": expand, "start": start + self.page_size})
            payload["_links"] = {"next": f"/rest/api/content/search?{more}"}
        return payload
