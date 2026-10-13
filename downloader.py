import hashlib
import re
import time
from pathlib import Path

from client import KurocoreClient
from config import DEFAULT_DOWNLOAD_DIR, MAX_DOWNLOADS_PER_SESSION
from parser import IllustDetail
import database as db


class DownloadSession:
    def __init__(self, client: KurocoreClient, conn, download_dir: str = DEFAULT_DOWNLOAD_DIR):
        self.client = client
        self.conn = conn
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.downloaded_count = 0
        self.on_progress = None
        self.on_error = None

    def _report(self, illust_id, title, file_idx, file_total, status, detail=""):
        if self.on_progress:
            self.on_progress({
                "illust_id": illust_id,
                "title": title,
                "file_idx": file_idx,
                "file_total": file_total,
                "status": status,
                "detail": detail,
            })

    @property
    def remaining(self) -> int:
        return MAX_DOWNLOADS_PER_SESSION - self.downloaded_count

    def download_detail(self, detail: IllustDetail) -> bool:
        if self.remaining <= 0:
            print(f"  [!] Session limit reached ({MAX_DOWNLOADS_PER_SESSION}). Restart to continue.")
            return False

        if db.exists_by_id(self.conn, detail.illust_id):
            print(f"  [=] {detail.illust_id} already downloaded, skipping.")
            return True

        db.upsert(self.conn, detail)

        if self.on_error:
            self.on_error({"illust_id": detail.illust_id, "title": detail.title, "error": ""})

        safe_title = _safe_filename(detail.title) or detail.illust_id
        illust_dir = self.download_dir / f"{detail.illust_id}_{safe_title}"
        illust_dir.mkdir(parents=True, exist_ok=True)

        saved_paths = []
        for i, url in enumerate(detail.image_urls):
            if self.remaining <= 0:
                print("  [!] Session limit reached mid-download.")
                break

            ext = Path(url).suffix or ".jpg"
            filename = f"{detail.illust_id}_p{i:02d}{ext}"
            filepath = illust_dir / filename

            try:
                start_time = time.time()
                content = self.client.download(url)
                file_hash = hashlib.sha256(content).hexdigest()

                if db.exists_by_hash(self.conn, file_hash):
                    print(f"    [{i+1}/{len(detail.image_urls)}] duplicate content, skipping.")
                    self._report(detail.illust_id, detail.title, i+1, len(detail.image_urls), "duplicate", "内容重复，跳过")
                    continue

                filepath.write_bytes(content)
                saved_paths.append(str(filepath))
                self.downloaded_count += 1
                print(f"    [{i+1}/{len(detail.image_urls)}] saved {filename} ({len(content)//1024}KB)")
                elapsed = time.time() - start_time
                self._report(detail.illust_id, detail.title, i+1, len(detail.image_urls), "saved",
                            f"{filename} ({len(content)//1024}KB, {elapsed:.1f}s)")

            except Exception as e:
                print(f"    [{i+1}/{len(detail.image_urls)}] ERROR: {e}")
                self._report(detail.illust_id, detail.title, i+1, len(detail.image_urls), "error", str(e))

        if saved_paths:
            combined_hash = hashlib.sha256("|".join(saved_paths).encode()).hexdigest()
            db.mark_completed(self.conn, detail.illust_id, saved_paths[0], combined_hash)
            print(f"  [OK] {detail.illust_id} '{detail.title}' ({len(saved_paths)} files)")
            self._report(detail.illust_id, detail.title, 0, len(detail.image_urls), "done",
                        f"完成 {len(saved_paths)}/{len(detail.image_urls)} 张")
            return True
        else:
            db.mark_failed(self.conn, detail.illust_id)
            print(f"  [FAIL] {detail.illust_id}")
            if self.on_error:
                self.on_error({"illust_id": detail.illust_id, "title": detail.title, "error": "所有图片下载失败"})
            self._report(detail.illust_id, detail.title, 0, 0, "failed", "所有图片下载失败")
            return False


def _safe_filename(name: str, max_len: int = 60) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', '', name).strip()
    return cleaned[:max_len]
