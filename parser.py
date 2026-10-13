from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import BASE_URL


@dataclass
class SearchResult:
    illust_id: str
    title: str
    thumbnail_url: str
    page_count: int = 1
    author_name: str = ""
    author_url: str = ""
    is_r18: bool = False
    avatar_url: str = ""


@dataclass
class IllustDetail:
    illust_id: str
    title: str
    author_name: str = ""
    author_pixiv_id: str = ""
    pixiv_url: str = ""
    release_date: str = ""
    tags: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    kurocore_url: str = ""


def parse_search_page(html: str) -> tuple[list[SearchResult], int]:
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for card in soup.select("div.illust"):
        link = card.select_one("a.cover")
        if link is None:
            continue

        illust_id = link.get("href", "").split("/")[-1]
        title = link.get("title", "").strip()
        img = link.select_one("img")
        thumbnail = img.get("data-original", "") if img else ""

        manga_badge = card.select_one(".illust-manga")
        page_count = 1
        if manga_badge:
            try:
                page_count = int(manga_badge.get_text(strip=True))
            except ValueError:
                pass

        author_link = card.select_one("a.member")
        author_name = ""
        author_url = ""
        if author_link:
            author_url = author_link.get("href", "")
            strong = author_link.select_one("strong")
            if strong:
                author_name = strong.get_text(strip=True)

        is_r18 = card.select_one(".restrict-alert") is not None

        avatar_url = ""
        if author_link:
            icon = author_link.select_one("img.member-icon")
            if icon:
                avatar_url = icon.get("src", "")

        results.append(SearchResult(
            illust_id=illust_id,
            title=title,
            thumbnail_url=thumbnail,
            page_count=page_count,
            author_name=author_name,
            author_url=author_url,
            is_r18=is_r18,
            avatar_url=avatar_url,
        ))

    total_pages = _parse_total_pages(soup)
    return results, total_pages


def _parse_total_pages(soup: BeautifulSoup) -> int:
    last_page = soup.select_one("ul.pagination li:last-child a.page-link")
    if last_page:
        try:
            return int(last_page.get("data-page", 1))
        except ValueError:
            pass
    active = soup.select_one("ul.pagination li.page-item span.page-input.active")
    if active:
        try:
            return int(active.get_text(strip=True))
        except ValueError:
            pass
    return 1


def parse_detail_page(html: str, illust_id: str) -> IllustDetail:
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    pixiv_url = ""
    title_link = soup.select_one("#main-illust h3 a")
    if title_link:
        title = title_link.get_text(strip=True)
        pixiv_url = title_link.get("href", "")

    author_name = ""
    author_pixiv_id = ""
    author_link = soup.select_one("#member-info a.member")
    if author_link:
        strong = author_link.select_one("strong")
        if strong:
            author_name = strong.get_text(strip=True)
        href = author_link.get("href", "")
        if "/users/" in href:
            author_pixiv_id = href.rstrip("/").split("/")[-1]

    release_date = ""
    date_p = soup.select_one("#main-illust p")
    if date_p and "Release Date" in date_p.get_text():
        text = date_p.get_text(strip=True)
        release_date = text.split(":", 1)[-1].strip()

    tags = [t.get_text(strip=True) for t in soup.select("#tags .tag-name")]

    image_urls = []
    for img in soup.select("img[data-original]"):
        src = img["data-original"]
        if "/i/" in src and illust_id in src:
            image_urls.append(_to_large_url(src))

    return IllustDetail(
        illust_id=illust_id,
        title=title,
        author_name=author_name,
        author_pixiv_id=author_pixiv_id,
        pixiv_url=pixiv_url,
        release_date=release_date,
        tags=tags,
        image_urls=list(dict.fromkeys(image_urls)),
        kurocore_url=f"{BASE_URL}/illust/{illust_id}",
    )


def _to_large_url(url: str) -> str:
    return url.replace("/i/sm/", "/i/lg/").replace("/i/md/", "/i/lg/")


def search_url(query: str, page: int = 1) -> str:
    from urllib.parse import quote
    return f"{BASE_URL}/illust?q={quote(query)}&page={page}"


def parse_hot_tags(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [a.get_text(strip=True) for a in soup.select(".tag-chip")]


def detail_url(illust_id: str) -> str:
    return f"{BASE_URL}/illust/{illust_id}"
