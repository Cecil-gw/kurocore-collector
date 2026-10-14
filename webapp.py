import json
import random
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import mimetypes

from client import KurocoreClient
from config import BASE_URL, DEFAULT_DB_PATH, DEFAULT_DOWNLOAD_DIR, MAX_DOWNLOADS_PER_SESSION
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
random_collect_active = False


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
            d = dict(progress)
            d["session_downloads"] = download_session.downloaded_count
            d["session_max"] = MAX_DOWNLOADS_PER_SESSION
            self._json_response(d)
        elif path == "/api/library":
            self._api_library(params)
        elif path == "/api/stats":
            self._json_response(db.get_stats(conn))
        elif path == "/api/contact_sheet":
            self._api_contact_sheet()
        elif path == "/api/delete":
            self._api_delete(params)
        elif path == "/api/clear_all":
            self._api_clear_all()
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
        elif self.path == "/api/random_collect":
            self._api_random_collect()
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
        start_page = int(params.get("start_page", ["1"])[0])
        max_pages = int(params.get("pages", ["3"])[0])
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
            current_page = start_page
            total_pages = 1
            total_found = 0
            pages_fetched = 0
            while pages_fetched < max_pages and current_page <= total_pages:
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
                pages_fetched += 1

            has_more = current_page <= total_pages
            send_event("done", {"total_found": total_found, "pages_fetched": pages_fetched, "next_page": current_page, "total_pages": total_pages, "has_more": has_more})
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

    def _api_delete(self, params):
        illust_id = params.get("id", [""])[0]
        if not illust_id:
            self._json_response({"error": "no id"}, 400)
            return
        row = conn.execute(
            "SELECT file_path FROM illusts WHERE kurocore_id = ?",
            (illust_id,),
        ).fetchone()
        if not row:
            self._json_response({"error": "not found"}, 404)
            return
        file_path = row["file_path"]
        if file_path:
            illust_dir = Path(file_path).parent
            if illust_dir.exists() and illust_dir.name.startswith(illust_id):
                import shutil
                shutil.rmtree(illust_dir, ignore_errors=True)
        with db._lock:
            conn.execute("DELETE FROM illusts WHERE kurocore_id = ?", (illust_id,))
            conn.commit()
        self._json_response({"status": "deleted", "id": illust_id})

    def _api_clear_all(self):
        rows = conn.execute("SELECT kurocore_id, file_path FROM illusts WHERE status='completed'").fetchall()
        deleted_dirs = set()
        for row in rows:
            fp = row["file_path"]
            if fp:
                illust_dir = Path(fp).parent
                if illust_dir.exists():
                    deleted_dirs.add(illust_dir)
        import shutil
        for d in deleted_dirs:
            shutil.rmtree(d, ignore_errors=True)
        with db._lock:
            conn.execute("DELETE FROM illusts")
            conn.commit()
        self._json_response({"status": "cleared", "deleted": len(rows)})

    def _api_random_collect(self):
        global random_collect_active
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        max_total = body.get("max", 0)
        safe = body.get("safe", True)
        per_tag = body.get("per_tag", 5)
        if random_collect_active:
            self._json_response({"error": "already running"}, 409)
            return
        random_collect_active = True
        thread = threading.Thread(target=_random_collect_worker, args=(max_total, safe, per_tag), daemon=True)
        thread.start()
        self._json_response({"status": "started"})

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
                progress["error"] = f"Session limit reached ({MAX_DOWNLOADS_PER_SESSION})"
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


def _random_collect_worker(max_total, safe, per_tag):
    global random_collect_active
    try:
        if _suggest_cache is None or len(_suggest_cache) < 10:
            r = client.session.get(f"{BASE_URL}/asset/data/k.json", timeout=15)
            tags = r.json()
        else:
            tags = _suggest_cache
        if safe:
            tags = [t for t in tags if "r-18" not in t.lower()]
        if len(tags) < 10:
            return

        existing = {row["kurocore_id"] for row in conn.execute(
            "SELECT kurocore_id FROM illusts WHERE status='completed'"
        ).fetchall()}

        progress["running"] = True
        progress["total"] = max_total
        progress["done"] = 0
        progress["current"] = "随机采集中..."

        collected = 0
        used_tags = set()
        while (max_total <= 0 or collected < max_total) and len(used_tags) < 200:
            tag = tags[random.randint(0, len(tags) - 1)]
            if tag in used_tags:
                continue
            used_tags.add(tag)
            progress["current"] = f"随机采集 [{tag}] {collected}/{max_total}"

            try:
                html = client.get_html(site_parser.search_url(tag, 1))
                results, _ = site_parser.parse_search_page(html)
                if safe:
                    results = [r for r in results if not r.is_r18]
                new_ids = [r.illust_id for r in results if r.illust_id not in existing][:per_tag]
                if not new_ids:
                    continue
                for iid in new_ids:
                    existing.add(iid)
                for iid in new_ids:
                    if collected >= max_total or download_session.remaining <= 0:
                        break
                    try:
                        d_html = client.get_html(site_parser.detail_url(iid))
                        detail = site_parser.parse_detail_page(d_html, iid)
                        if detail.image_urls:
                            download_session.download_detail(detail)
                            collected += 1
                            progress["done"] = collected
                    except Exception:
                        pass
            except Exception:
                continue
        progress["current"] = f"随机采集完成 ({collected} 张)"
    finally:
        random_collect_active = False
        progress["running"] = False


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
