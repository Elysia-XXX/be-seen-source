"""Bounded public article reading. Search evidence is never overwritten."""
import asyncio
import hashlib
import http.client
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit
from .db import now
from .domain import canonical_url

MAX_BYTES = 1_000_000
MAX_TEXT = 12000


def article_key(url):
    return 'article:' + hashlib.sha256((canonical_url(url) or url).encode()).hexdigest()[:24]


def public_target(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('只读取公开 HTTP(S) 页面。')
    if p.port not in (None, 80 if p.scheme == 'http' else 443):
        raise ValueError('不读取非标准端口。')
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(a[4][0] for a in addresses))
    if not ips or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in ips):
        raise ValueError('该链接不是可读取的公网地址。')
    return p, ips[0]


class ArticleText(HTMLParser):
    """Prefer article/main; strip scripts, navigation and hidden content."""
    excluded = {'script', 'style', 'nav', 'header', 'footer', 'noscript', 'svg', 'form', 'aside', 'template'}
    void = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.all, self.main = [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = tag in self.excluded or 'hidden' in attrs or attrs.get('aria-hidden') == 'true' or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', attrs.get('style', ''), re.I))
        if tag not in self.void:
            self.stack.append((tag, hidden))

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if any(hidden for _, hidden in self.stack):
            return
        text = re.sub(r'\s+', ' ', data).strip()
        if text:
            self.all.append(text)
            if any(t in ('article', 'main') for t, _ in self.stack):
                self.main.append(text)

    def result(self):
        return '\n'.join(self.main or self.all)


def read_public_article(url):
    """Resolve/check each redirect and pin the connection to the checked IP.

    No browser, credentials, cookies, JavaScript execution or proxy inheritance.
    TLS still verifies the original hostname. Limits include redirects.
    """
    deadline, current = time.monotonic() + 20, url
    try:
        for redirect in range(4):
            parsed, address = public_target(current)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('读取超时。')
            cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
            conn = cls(parsed.hostname, port=parsed.port, timeout=remaining)
            # Pin the socket, retaining the logical host for Host and TLS SNI.
            conn._create_connection = lambda dest, timeout, source_address=None: socket.create_connection((address, dest[1]), timeout, source_address)
            try:
                path = quote((parsed.path or '/') + ('?' + parsed.query if parsed.query else ''), safe="/%?:@!$&'()*+,;=-._~")
                conn.request('GET', path, headers={'User-Agent': 'BeSeenResearch/1.0', 'Accept': 'text/html,text/plain', 'Accept-Encoding': 'identity'})
                response = conn.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location')
                    if not location or redirect == 3:
                        raise ValueError('重定向次数过多或缺少目标。')
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    raise ValueError(f'原站返回 HTTP {response.status}，未读取正文。')
                mime = response.getheader('Content-Type', '').lower()
                if not any(t in mime for t in ('text/html', 'text/plain', 'application/xhtml+xml')):
                    raise ValueError('仅支持 HTML 或纯文本文章。')
                if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                    raise ValueError('原站返回压缩内容，未读取正文。')
                body = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError('读取超时。')
                    if conn.sock:
                        conn.sock.settimeout(remaining)
                    chunk = response.read1(min(65536, MAX_BYTES + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError('页面过大，未读取正文。')
                encoding = re.search(r'charset\s*=\s*["\']?([\w-]+)', mime)
                if not encoding:
                    encoding = re.search(r'charset\s*=\s*["\']?([\w-]+)', bytes(body[:4096]).decode('ascii', errors='ignore'))
                codec = encoding.group(1) if encoding else 'utf-8'
                try:
                    decoded = body.decode(codec, errors='replace')
                except LookupError:
                    decoded = body.decode('utf-8', errors='replace')
                if 'text/plain' in mime:
                    text = decoded.strip()
                else:
                    parser = ArticleText(); parser.feed(decoded); text = parser.result()
                if len(text) < 150 or any(x in text[:1000] for x in ('访问验证', '安全验证', '请完成验证', '请登录后', 'Just a moment...', 'Access Denied')):
                    raise ValueError('未获得足够正文，可能需要登录、验证或脚本加载。')
                return {'status': 'read', 'url': current, 'fetched_at': now(), 'text': text[:MAX_TEXT], 'truncated': len(text) > MAX_TEXT, 'sha256': hashlib.sha256(body).hexdigest(), 'method': 'static-html-v1'}
            finally:
                conn.close()
    except ValueError as exc:
        reason = str(exc)
    except Exception:
        reason = '原站连接失败或超时；搜索摘要仍保留。'
    return {'status': 'unavailable', 'url': url, 'fetched_at': now(), 'text': '', 'reason': reason}


async def read_article(url):
    return await asyncio.to_thread(read_public_article, url)


def with_articles(pages, steps):
    return [{**p, **({'article': steps[article_key(p['url'])]['output']} if article_key(p['url']) in steps and steps[article_key(p['url'])]['status'] == 'completed' else {})} for p in pages]
