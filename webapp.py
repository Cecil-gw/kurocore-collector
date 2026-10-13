import json
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import mimetypes

from client import KurocoreClient
from config import BASE_URL, DEFAULT_DB_PATH, DEFAULT_DOWNLOAD_DIR
import database as db
import parser as site_parser
from downloader import DownloadSession


client = KurocoreClient()
conn = db.init_db(DEFAULT_DB_PATH)
download_session = DownloadSession(client, conn, DEFAULT_DOWNLOAD_DIR)
progress = {"running": False, "total": 0, "done": 0, "current": "", "error": "", "detail": "", "file_idx": 0, "file_total": 0}
errors = []
_suggest_cache = None
download_queue = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._serve_file("static/index.html", "text/html; charset=utf-8")
        elif path == "/api/search":
            self._api_search(params)
        elif path == "/api/search_stream":
            self._api_search_stream(params)
        elif path == "/api/thumb":
            self._api_thumb(params)
        elif path == "/api/progress":
            self._json_response(progress)
        elif path == "/api/library":
            self._api_library(params)
        elif path == "/api/stats":
            self._json_response(db.get_stats(conn))
        elif path == "/api/contact_sheet":
            self._api_contact_sheet()
        elif path == "/api/hot_tags":
            self._api_hot_tags()
        elif path == "/api/history":
            if "save" in params:
                db.save_search(conn, params["save"][0])
            self._json_response({"history": db.get_history(conn)})
        elif path == "/api/suggest":
            self._api_suggest()
        elif path.startswith("/gallery/"):
            self._serve_gallery_file(path)
        else:
            self._serve_file(f"static{path}", None)

    def do_POST(self):
        if self.path == "/api/download":
            self._api_download()
        elif self.path == "/api/download_avatars":
            self._api_download_avatars()
        else:
            self._json_response({"error": "not found"}, 404)

    def _api_download_avatars(self):
        import hashlib
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        avatars = body.get("avatars", [])
        if not avatars:
            self._json_response({"error": "no avatars"}, 400)
            return

        avatar_dir = Path(DEFAULT_DOWNLOAD_DIR) / "avatars"
        avatar_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for item in avatars:
            name = item.get("name", "").strip()
            url = item.get("url", "")
            if not url or not name:
                continue
            safe_name = "".join(c for c in name if c not in r'<>:"/\|?*').strip()[:50]
            if not safe_name:
                safe_name = hashlib.md5(url.encode()).hexdigest()[:12]
            ext = url.rsplit(".", 1)[-1] if "." in url.rsplit("/", 1)[-1] else "jpg"
            filepath = avatar_dir / f"{safe_name}.{ext}"
            if filepath.exists():
                continue
            try:
                content = client.download(url)
                filepath.write_bytes(content)
                saved += 1
            except Exception:
                pass
        self._json_response({"saved": saved, "total": len(avatars), "dir": str(avatar_dir)})

    def _api_search(self, params):
        query = params.get("q", [""])[0].strip()
        page = int(params.get("page", ["1"])[0])
        max_results = int(params.get("max", ["100"])[0])
        safe_mode = params.get("safe", ["0"])[0] == "1"
        if not query:
            self._json_response({"results": [], "total_pages": 0, "page": 1})
            return
        try:
            all_results = []
            current_page = page
            total_pages = 1
            while len(all_results) < max_results and current_page <= total_pages:
                html = client.get_html(site_parser.search_url(query, current_page))
                page_results, total_pages = site_parser.parse_search_page(html)
                if safe_mode:
                    page_results = [r for r in page_results if not r.is_r18]
                all_results.extend(page_results)
                current_page += 1
                if not page_results:
                    break
            results = all_results[:max_results]
            self._json_response({
                "results": [vars(r) for r in results],
                "total_pages": total_pages,
                "page": current_page - 1,
                "query": query,
            })
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _api_search_stream(self, params):
        query = params.get("q", [""])[0].strip()
        max_results = int(params.get("max", ["100"])[0])
        safe_mode = params.get("safe", ["0"])[0] == "1"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        def send_event(event, data):
            payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()

        try:
            current_page = 1
            total_pages = 1
            total_found = 0
            while total_found < max_results and current_page <= total_pages:
                html = client.get_html(site_parser.search_url(query, current_page))
                page_results, total_pages = site_parser.parse_search_page(html)
                if safe_mode:
                    page_results = [r for r in page_results if not r.is_r18]

                if not page_results:
                    break

                batch = [vars(r) for r in page_results]
                total_found += len(batch)
                send_event("results", {
                    "results": batch,
                    "page": current_page,
                    "total_pages": total_pages,
                    "total_found": total_found,
                })
                current_page += 1

            send_event("done", {"total_found": total_found, "pages_fetched": current_page - 1})
        except Exception as e:
            try:
                send_event("error", {"message": str(e)})
            except Exception:
                pass

    def _api_thumb(self, params):
        url = params.get("url", [""])[0]
        if not url:
            self._empty_image()
            return
        try:
            content = client.session.get(url, timeout=10).content
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            self._empty_image()

    def _api_library(self, params):
        rows = conn.execute(
            "SELECT * FROM illusts WHERE status='completed' ORDER BY downloaded_at DESC LIMIT 200"
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.get("tags", "[]"))
            pages = []
            file_path = d.get("file_path", "")
            if file_path:
                normalized = file_path.replace("\\", "/")
                if normalized.startswith("gallery/"):
                    d["thumb_url"] = "/" + normalized
                    illust_dir = Path(file_path).parent
                    if illust_dir.exists():
                        for img in sorted(illust_dir.glob("*.jpg")):
                            rel = img.relative_to(DEFAULT_DOWNLOAD_DIR).as_posix()
                            pages.append("/" + rel)
                    d["pages"] = pages
                elif "/gallery/" in normalized:
                    d["thumb_url"] = "/" + normalized[normalized.index("gallery/"):]
                    d["pages"] = []
                else:
                    d["thumb_url"] = ""
                    d["pages"] = []
            else:
                d["thumb_url"] = ""
                d["pages"] = []
            items.append(d)
        self._json_response({"items": items, "total": len(items)})

    def _api_hot_tags(self):
        try:
            html = client.get_html(f"{BASE_URL}/")
            tags = site_parser.parse_hot_tags(html)
            self._json_response({"tags": tags})
        except Exception as e:
            self._json_response({"tags": [], "error": str(e)})

    def _api_suggest(self):
        global _suggest_cache
        if _suggest_cache is None:
            try:
                r = client.session.get(f"{BASE_URL}/asset/data/k.json", timeout=15)
                _suggest_cache = r.json()
            except Exception:
                _suggest_cache = []
        self._json_response({"tags": _suggest_cache})

    def _api_contact_sheet(self):
        import io
        from contact_sheet import collect_images, generate as gen_sheet
        buf = io.BytesIO()
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        gen_sheet(DEFAULT_DOWNLOAD_DIR, tmp.name)
        with open(tmp.name, "rb") as f:
            content = f.read()
        os.unlink(tmp.name)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _api_download(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        illust_ids = body.get("illust_ids", [])
        if not illust_ids:
            self._json_response({"error": "no illust_ids"}, 400)
            return
        if progress["running"]:
            download_queue.extend(illust_ids)
            self._json_response({"status": "queued", "count": len(illust_ids), "queue_size": len(download_queue)})
            return

        progress.update({"running": True, "total": len(illust_ids), "done": 0, "current": "", "error": ""})
        errors.clear()
        thread = threading.Thread(target=_download_worker, args=(illust_ids,), daemon=True)
        thread.start()
        self._json_response({"status": "started", "count": len(illust_ids)})

    def _json_response(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, filepath, content_type):
        path = Path(filepath)
        if not path.exists():
            self.send_error(404)
            return
        if content_type is None:
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_gallery_file(self, path):
        from urllib.parse import unquote
        decoded = unquote(path[len("/gallery/"):])
        full = Path(DEFAULT_DOWNLOAD_DIR) / decoded
        if not full.exists() or not full.is_file():
            self.send_error(404)
            return
        content = full.read_bytes()
        ct = mimetypes.guess_type(str(full))[0] or "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _empty_image(self):
        self.send_response(204)
        self.end_headers()


def _download_worker(illust_ids):
    all_ids = list(illust_ids)
    errors = []

    def on_progress(info):
        progress["current"] = info["title"] or info["illust_id"]
        progress["file_idx"] = info["file_idx"]
        progress["file_total"] = info["file_total"]
        progress["detail"] = info["detail"]
        progress["status"] = info["status"]

    def on_error(info):
        errors.append(info)
        progress["error"] = f"{info['illust_id']}: {info['error']}"

    download_session.on_progress = on_progress
    download_session.on_error = on_error

    while True:
        if download_queue:
            all_ids.extend(download_queue)
            download_queue.clear()
        if not all_ids:
            break
        current_batch = all_ids
        all_ids = []
        for illust_id in current_batch:
            if download_session.remaining <= 0:
                progress["error"] = "Session limit reached (50)"
                all_ids.clear()
                break
            progress["current"] = illust_id
            try:
                html = client.get_html(site_parser.detail_url(illust_id))
                detail = site_parser.parse_detail_page(html, illust_id)
                if detail.image_urls:
                    download_session.download_detail(detail)
                else:
                    on_error({"illust_id": illust_id, "title": "", "error": "未找到图片"})
            except Exception as e:
                on_error({"illust_id": illust_id, "title": "", "error": str(e)})
            progress["done"] += 1
            time.sleep(1.0)

    progress["running"] = False
    progress["current"] = ""
    progress["errors"] = errors


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8888), Handler)
    print("Kurocore Collector web UI")
    print("http://127.0.0.1:8888")
    print("Ctrl+C to stop")
    threading.Thread(target=_preload_suggest, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def _preload_suggest():
    global _suggest_cache
    try:
        r = client.session.get(f"{BASE_URL}/asset/data/k.json", timeout=15)
        _suggest_cache = r.json()
        print(f"Suggest cache loaded: {len(_suggest_cache)} tags")
    except Exception as e:
        print(f"Suggest preload failed: {e}")


if __name__ == "__main__":
    main()
