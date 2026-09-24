import logging
import re
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from typing import Tuple, List, Optional

from config import data
from config.config import get_config
from i18n.i18n import _
from models.song import Lyrics
from utils.helpers import prompt_response, http_get
from utils.string import is_empty

ATWIKI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0 Safari/537.36"
}
ATWIKI_TIMEOUT = 20


def parse_at_wiki_header(header: str) -> List[Tuple[str, str]]:
    lines = header.split("\n")
    result: List[Tuple[str, str]] = []
    for line in lines:
        index = line.find("：")
        if index != -1:
            result.append((line[:index].strip(), line[index + 1:].strip()))
    return result


def is_lyrics(line: str) -> bool:
    line = line.strip()
    return not (line == "歌詞" or
                line == data.name_japanese or
                (data.name_japanese in line and ("オリジナル" in line or
                                                 re.match("[【『]+", line) or
                                                 "歌詞" in line)) or
                (("転載" in line or "转载" in line or "取り" in line)
                 and re.match("[(（]+", line))
                )


def strip_initial_lines(lines: List[str]) -> List[str]:
    index = 0
    while index < len(lines):
        if not is_empty(lines[index]) and is_lyrics(lines[index]):
            return lines[index:]
        index += 1
    return []


def parse_at_wiki_body(body: str) -> str:
    lines = body.split("\n")
    result = []
    lines = strip_initial_lines(lines)
    state = 0
    index = 0
    while index < len(lines):
        if is_empty(lines[index]):
            state += 1
        else:
            if state > 2:
                result.append("")
            result.append(lines[index].strip())
            state = 0
        index += 1
    return "\n".join(result)


def shorten_url(url: str) -> str:
    if "&pageid=" in url:
        page_id = url.split("pageid=")[1]
        url = f"https://w.atwiki.jp/vocaloidchly/pages/{page_id}.html"
    return url


def find_at_wiki_page(name: str, urls: List[str], producer: str) -> Optional[str]:
    for url in urls:
        response = http_get(url, use_proxy=True, headers=ATWIKI_HEADERS, timeout=ATWIKI_TIMEOUT)
        if response.status_code == 403:
            continue
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        wiki_body = soup.find("div", {"id": "wikibody"})
        result_list = wiki_body.find("ul") if wiki_body else None
        if result_list is None:
            continue
        for item in result_list.find_all("li"):
            link = item.find("a")
            if not link:
                continue
            page_name = link.get_text(strip=True)
            if page_name in {name, name + "/" + producer}:
                return "https:" + link.get("href")
    fallback_url = "https://html.duckduckgo.com/html/?q=" + quote_plus(
        f'site:w.atwiki.jp/hmiku "{name}"')
    try:
        response = http_get(fallback_url, use_proxy=True, headers=ATWIKI_HEADERS, timeout=ATWIKI_TIMEOUT)
        if response.ok:
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.select("a.result__a[href]"):
                href = link.get("href")
                if "w.atwiki.jp/hmiku/pages/" in href:
                    return href.split("&rut=")[-1]
    except Exception:
        logging.debug("External AtWiki search fallback failed.", exc_info=True)
    return None


def get_vocaloid_collection_info(name: str, producer: str = "") -> Optional[Tuple[str, Optional[str]]]:
    url_jap = "https://w.atwiki.jp/hmiku/search?andor=and&keyword={}&search_field=source"
    try:
        found = find_at_wiki_page(name, [url_jap.format(name + "+" + producer),
                                         url_jap.format(name)], producer)
        if found is None:
            return None
        response = http_get(found, use_proxy=True, headers=ATWIKI_HEADERS, timeout=ATWIKI_TIMEOUT)
        response.raise_for_status()
        page = response.text
        if "ボカコレ" not in page:
            return None
        collection_match = re.search(r"ボカコレ20\d{2}[春夏秋冬]", page)
        if not collection_match:
            return None
        rank_match = re.search(r"TOP100.{0,200}?(?:第\s*)?(\d+)\s*(?:位|名)", page, re.DOTALL)
        return collection_match.group(0), rank_match.group(1) if rank_match else None
    except Exception as e:
        logging.warning("Unable to check The VOCALOID Collection for %s: %s", name, e)
        return None


def get_vocaloid_collection(name: str, producer: str = "") -> Optional[str]:
    info = get_vocaloid_collection_info(name, producer)
    return info[0] if info else None


def get_at_wiki_body(name: str, urls: List[str], lang: str, producer: str) -> Optional[Lyrics]:
    try:
        found = find_at_wiki_page(name, urls, producer)
        if found is None:
            return None
        logging.debug("At wiki url " + found)
        response = http_get(found, use_proxy=True, headers=ATWIKI_HEADERS, timeout=ATWIKI_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        # remove last modify message from body
        for elem in soup.find_all("div", attrs={'class': 'atwiki-lastmodify'}):
            elem.decompose()
        res = parse_body(name, soup.find("div", {"id": "wikibody"}).text)
        translator = [s for s in res[0] if s[0] == "翻译" or s[0] == "翻譯"]
        if len(translator) == 0:
            translator = "ERROR!"
        else:
            translator = translator[0][1]
        return Lyrics(staff=res[0], source_name="VOCALOID中文歌词wiki", source_url=shorten_url(found), lyrics_chs=res[1],
                      translator=translator)
    except Exception as e:
        logging.error(e)
        logging.error("An error occurred while fetching " + lang + " lyrics from atwiki. Falling back...")
        return None


def parse_body(name: str, text: str) -> (List[Tuple[str, str]], str):
    """
    Parse the body text in atwiki to remove extraneous things and
    extract artist roles and names.
    :param name: Name of the song.
    :param text: Main text to be parsed.
    :return: A tuple
    """
    index_comment = text.find("\nコメント\n")
    if index_comment != -1:
        text = text[:index_comment]
    split_index = max(text.find("翻譯"), text.find("翻译"))
    if split_index == -1:
        split_index = text.find("：")
    divider = text.find("\n", split_index)
    header = text[:divider]
    body = text[divider:]
    keywords = ["ブロマガより転載", "\n歌詞\n", "\n" + name + "\n"]
    index = max([body.find(k) for k in keywords])
    if index == -1:
        index = 0
    index = body.find("\n", index) + 1
    body = body[index:]
    return parse_at_wiki_header(header), parse_at_wiki_body(body)


def get_japanese_lyrics(name: str, producer: str = "") -> str:
    logging.info(_("jap_atwiki"))
    url_jap = "https://w.atwiki.jp/hmiku/search?andor=and&keyword={}&search_field=source"
    res = get_at_wiki_body(name, [url_jap.format(name + "+" + producer), url_jap.format(name)], "Japanese", producer)
    return res.lyrics_chs if res else ""


def get_chinese_lyrics(name: str, producer: str = "") -> Optional[Lyrics]:
    logging.info(_("chs_atwiki"))
    url_chs = "https://w.atwiki.jp/vocaloidchly/search?andor=and&keyword={}&search_field=source"
    return get_at_wiki_body(name, [url_chs.format(name + '+' + producer), url_chs.format(name)], "Chinese", producer)
