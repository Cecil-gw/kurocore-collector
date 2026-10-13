import sys
import time

from client import KurocoreClient, CloudflareChallengeError
from config import DEFAULT_DB_PATH, DEFAULT_DOWNLOAD_DIR
import database as db
import parser as site_parser
from downloader import DownloadSession


BANNER = """
╔══════════════════════════════════╗
║   Kurocore Illustration Collector ║
╚══════════════════════════════════╝
  For personal study only. Please respect artists' copyright.
  Visit original works on Pixiv when possible.
"""


def main():
    print(BANNER)

    client = KurocoreClient()
    conn = db.init_db(DEFAULT_DB_PATH)
    session = DownloadSession(client, conn, DEFAULT_DOWNLOAD_DIR)

    stats = db.get_stats(conn)
    if stats["total"] > 0:
        print(f"Library: {stats['completed']} completed, {stats['pending']} pending, "
              f"{stats['failed']} failed ({stats['total']} total)")

    while True:
        query = input("\nSearch keyword (or 'q' to quit, 's' for stats): ").strip()
        if not query:
            continue
        if query == "q":
            break
        if query == "s":
            print(f"  {db.get_stats(conn)}")
            continue

        run_search(client, conn, session, query)

    conn.close()
    print("Goodbye!")


def run_search(client, conn, download_session, query):
    page = 1
    while True:
        url = site_parser.search_url(query, page)
        print(f"\nSearching '{query}' page {page}...")

        try:
            html = client.get_html(url)
        except CloudflareChallengeError:
            print("Cloudflare challenge detected. Try again later or use browser cookies.")
            return
        except Exception as e:
            print(f"Error fetching search page: {e}")
            return

        results, total_pages = site_parser.parse_search_page(html)
        if not results:
            print("No results found.")
            return

        print(f"\nPage {page}/{total_pages} ({len(results)} results):")
        print("-" * 70)
        for i, r in enumerate(results, 1):
            pages_str = f" ({r.page_count}p)" if r.page_count > 1 else ""
            print(f"  {i:3d}. [{r.illust_id}] {r.title} — {r.author_name}{pages_str}")
        print("-" * 70)

        choice = input(
            "Select (e.g. 1,3,5 | all | n=next | p=prev | q=quit): "
        ).strip().lower()

        if choice == "q":
            return
        if choice == "n":
            if page < total_pages:
                page += 1
            else:
                print("Already on last page.")
            continue
        if choice == "p":
            if page > 1:
                page -= 1
            else:
                print("Already on first page.")
            continue
        if choice == "all":
            selected = results
        else:
            try:
                indices = [int(x.strip()) - 1 for x in choice.split(",")]
                selected = [results[i] for i in indices if 0 <= i < len(results)]
            except (ValueError, IndexError):
                print("Invalid selection.")
                continue

        if not selected:
            print("Nothing selected.")
            continue

        _download_selected(client, conn, download_session, selected)


def _download_selected(client, conn, download_session, selected):
    print(f"\nDownloading {len(selected)} item(s)...")
    success = 0
    for i, result in enumerate(selected, 1):
        if download_session.remaining <= 0:
            print(f"  [!] Download limit reached. {success}/{len(selected)} completed.")
            break

        detail_url = site_parser.detail_url(result.illust_id)
        print(f"\n  [{i}/{len(selected)}] Fetching detail: {result.illust_id}")

        try:
            html = client.get_html(detail_url)
            detail = site_parser.parse_detail_page(html, result.illust_id)
        except Exception as e:
            print(f"    Error fetching detail: {e}")
            continue

        if not detail.image_urls:
            print(f"    No image URLs found.")
            continue

        if download_session.download_detail(detail):
            success += 1

    print(f"\nDone. {success}/{len(selected)} succeeded. "
          f"Session downloads: {download_session.downloaded_count}/50")


if __name__ == "__main__":
    main()
