BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BASE_URL = "https://kurocore.com"
CDN_PATTERN = "https://i{kurocore_cdn}.kurocore.com"

DEFAULT_DOWNLOAD_DIR = "gallery"
DEFAULT_DB_PATH = "gallery.db"

REQUEST_DELAY = 0.5
REQUEST_JITTER = 0.3
REQUEST_TIMEOUT = 20

PROXY_URL = "http://127.0.0.1:7890"

MAX_DOWNLOADS_PER_SESSION = 0

IMAGE_SIZE = "lg"

FILENAME_TEMPLATE = "{illust_id}_{page}_{safe_title}.jpg"
