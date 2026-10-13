import random
import time

import requests

from config import BROWSER_UA, REQUEST_DELAY, REQUEST_JITTER, REQUEST_TIMEOUT, PROXY_URL


class KurocoreClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
        })
        if PROXY_URL:
            self.session.proxies = {"http": PROXY_URL, "https": PROXY_URL}
        else:
            self._try_system_proxy()

    def _try_system_proxy(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            proxy, _ = winreg.QueryValueEx(key, "ProxyServer")
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if enabled and proxy:
                url = f"http://{proxy}"
                self.session.proxies = {"http": url, "https": url}
        except Exception:
            pass

    def get(self, url: str, **kwargs) -> requests.Response:
        time.sleep(REQUEST_DELAY + random.uniform(0, REQUEST_JITTER))
        response = self.session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        response.raise_for_status()
        if "Just a moment" in response.text:
            raise CloudflareChallengeError(url)
        return response

    def get_html(self, url: str) -> str:
        return self.get(url).text

    def download(self, url: str, timeout: int = REQUEST_TIMEOUT) -> bytes:
        response = self.session.get(url, timeout=timeout, stream=True)
        response.raise_for_status()
        return response.content


class CloudflareChallengeError(Exception):
    pass
