from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node | str"] = field(default_factory=list)

    def text(self) -> str:
        return clean_text("".join(child if isinstance(child, str) else child.text() for child in self.children))

    def descendants(self, tag: str | None = None) -> Iterable["Node"]:
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.descendants(tag)


class TreeParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {key.lower(): value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if node.tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse_html(source: str) -> Node:
    parser = TreeParser()
    parser.feed(source)
    parser.close()
    return parser.root


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def absolute_wiki_url(href: str) -> str | None:
    if not href or href.startswith(("#", "javascript:", "mailto:")):
        return None
    result = urllib.parse.urljoin("https://wiki.biligame.com/wqmt/", href)
    parsed = urllib.parse.urlsplit(result)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {"wiki.biligame.com", "patchwiki.biligame.com"}:
        return None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def page_title_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.unquote(parsed.path)
    marker = "/wqmt/"
    if marker not in path:
        return None
    title = path.split(marker, 1)[1].replace("_", " ").strip("/")
    return title or None


def file_title_from_url(url: str | None) -> str | None:
    title = page_title_from_url(url)
    if not title:
        return None
    if title.startswith(("文件:", "File:")):
        return "文件:" + title.split(":", 1)[1]
    return None


def node_links(node: Node) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for anchor in node.descendants("a"):
        url = absolute_wiki_url(anchor.attrs.get("href", ""))
        if url:
            links.append({"text": anchor.text(), "url": url, "title": page_title_from_url(url) or ""})
    return links


def node_image(node: Node) -> dict[str, str] | None:
    for anchor in node.descendants("a"):
        url = absolute_wiki_url(anchor.attrs.get("href", ""))
        file_title = file_title_from_url(url)
        if file_title:
            img = next(anchor.descendants("img"), None)
            src = absolute_wiki_url(img.attrs.get("src", "")) if img else None
            return {"file_title": file_title, "thumbnail_url": src or ""}
    for img in node.descendants("img"):
        src = absolute_wiki_url(img.attrs.get("src", ""))
        alt = clean_text(img.attrs.get("alt", ""))
        alt_url = absolute_wiki_url(alt) if alt.startswith(("https://", "//")) else None
        if alt_url:
            return {"file_title": None, "thumbnail_url": src or "", "current_url": alt_url}
        if alt and re.search(r"\.(?:png|jpe?g|webp)$", alt, re.I):
            return {"file_title": "文件:" + alt.removeprefix("文件:"), "thumbnail_url": src or ""}
    return None


def _class_tokens(node: Node) -> set[str]:
    return set(node.attrs.get("class", "").split())


def _first_descendant_with_class(node: Node, class_name: str) -> Node | None:
    return next((child for child in node.descendants() if class_name in _class_tokens(child)), None)


def parse_character_cards(source: str, page_title: str) -> tuple[list[dict], list[dict]]:
    root = parse_html(source)
    raw: list[dict] = []
    issues: list[dict] = []
    profession = None
    card_number = 0
    for node in root.descendants():
        if node.tag == "span" and "tab-panel" in _class_tokens(node):
            label = node.text()
            if label in {"坚韧", "狂暴", "诡秘", "精准", "异能", "启迪"}:
                profession = label
        if node.tag != "div" or "txd" not in _class_tokens(node):
            continue
        card_number += 1
        name_node = _first_descendant_with_class(node, "txl")
        id_node = _first_descendant_with_class(node, "txnum")
        link = _page_link(name_node, ("图鉴",))
        name = name_node.text() if name_node else None
        if link and not name:
            name = link["title"].split(":", 1)[-1]
        image = node_image(node)
        card_profession = None
        for img in node.descendants("img"):
            alt = clean_text(img.attrs.get("alt", ""))
            for label in ("坚韧", "狂暴", "诡秘", "精准", "异能", "启迪"):
                if alt.startswith(label) and "tjicon" in alt.lower():
                    card_profession = label
                    break
            if card_profession:
                break
        item = {
            "name": name or None,
            "mbcc_id": id_node.text() if id_node and id_node.text() else None,
            "profession": card_profession or profession,
            "rarity": None,
            "page": link,
            "image": image,
            "source": {"page_title": page_title, "card": card_number},
        }
        item["missing_fields"] = _missing(item, ("name", "mbcc_id", "profession", "rarity", "page", "image"))
        if item["name"]:
            raw.append(item)
        else:
            issues.append({"type": "character_card_without_name", "source": item["source"]})
    output: list[dict] = []
    by_identity: dict[tuple[str, str], int] = {}
    for item in raw:
        key = (str(item.get("name") or ""), str(item.get("mbcc_id") or ""))
        if key in by_identity:
            existing = output[by_identity[key]]
            if existing.get("profession") != item.get("profession"):
                issues.append({"type": "conflicting_character_profession", "name": item.get("name"), "values": [existing.get("profession"), item.get("profession")]})
            continue
        by_identity[key] = len(output)
        output.append(item)
    issues.extend(unique_issues(output, ("name", "mbcc_id", "page"), "character"))
    return output, issues


def tables(root: Node) -> Iterable[tuple[list[str], list[list[Node]]]]:
    for table in root.descendants("table"):
        rows: list[list[Node]] = []
        headers: list[str] = []
        for tr in table.descendants("tr"):
            direct_cells = [child for child in tr.children if isinstance(child, Node) and child.tag in {"th", "td"}]
            if not direct_cells:
                continue
            if not headers and any(cell.tag == "th" for cell in direct_cells):
                headers = [cell.text() for cell in direct_cells]
                continue
            rows.append(direct_cells)
        if headers and rows:
            yield headers, rows


def _header_index(headers: list[str], aliases: tuple[str, ...]) -> int | None:
    normalized = [clean_text(value).lower() for value in headers]
    for alias in aliases:
        alias = alias.lower()
        for index, value in enumerate(normalized):
            if alias == value or alias in value:
                return index
    return None


def _cell(cells: list[Node], index: int | None) -> Node | None:
    return cells[index] if index is not None and index < len(cells) else None


def _value(cells: list[Node], index: int | None) -> str | None:
    cell = _cell(cells, index)
    value = cell.text() if cell else ""
    if not value and cell:
        for img in cell.descendants("img"):
            alt = clean_text(img.attrs.get("alt", ""))
            if alt and not alt.startswith(("http://", "https://", "//")):
                value = re.sub(r"\.(?:png|jpe?g|webp|gif)$", "", alt, flags=re.I)
                break
    return value or None


def _page_link(cell: Node | None, excluded: tuple[str, ...] = ()) -> dict[str, str] | None:
    if not cell:
        return None
    for link in node_links(cell):
        title = link["title"]
        if not title or title.startswith(("文件:", "File:", "特殊:", "Special:")):
            continue
        if any(term in title for term in excluded):
            continue
        return {"title": title, "url": link["url"]}
    return None


def _missing(fields: dict[str, object], required: tuple[str, ...]) -> list[str]:
    return [name for name in required if not fields.get(name)]


def parse_characters(source: str, page_title: str) -> tuple[list[dict], list[dict]]:
    root = parse_html(source)
    output: list[dict] = []
    issues: list[dict] = []
    for headers, rows in tables(root):
        indices = {
            "name": _header_index(headers, ("禁闭者", "角色名", "姓名", "名称")),
            "mbcc_id": _header_index(headers, ("mbcc", "编号")),
            "profession": _header_index(headers, ("职业", "特化")),
            "rarity": _header_index(headers, ("稀有度", "品质", "评级")),
            "image": _header_index(headers, ("立绘", "卡面", "头像", "图片", "角色")),
        }
        if indices["name"] is None:
            continue
        for row_number, cells in enumerate(rows, 1):
            name_cell = _cell(cells, indices["name"])
            name = _value(cells, indices["name"])
            link = _page_link(name_cell, ("图鉴",))
            if link and (not name or len(name) > 40):
                name = link["title"].split("/", 1)[0]
            image = node_image(_cell(cells, indices["image"]) or name_cell) if name_cell else None
            item = {
                "name": name,
                "mbcc_id": _value(cells, indices["mbcc_id"]),
                "profession": _value(cells, indices["profession"]),
                "rarity": _value(cells, indices["rarity"]),
                "page": link,
                "image": image,
                "source": {"page_title": page_title, "row": row_number},
            }
            missing = _missing(item, ("name", "mbcc_id", "profession", "rarity", "page", "image"))
            item["missing_fields"] = missing
            if name:
                output.append(item)
            else:
                issues.append({"type": "character_row_without_name", "source": item["source"], "text": " | ".join(cell.text() for cell in cells)})
    if not output:
        issues.append({"type": "no_character_table_recognized", "page_title": page_title})
    issues.extend(unique_issues(output, ("name", "mbcc_id", "page"), "character"))
    return output, issues


def parse_skins(source: str, page_title: str) -> tuple[list[dict], list[dict]]:
    root = parse_html(source)
    output: list[dict] = []
    issues: list[dict] = []
    for headers, rows in tables(root):
        indices = {
            "character": _header_index(headers, ("所属角色", "角色", "禁闭者")),
            "name": _header_index(headers, ("装束名", "皮肤名", "装束", "皮肤", "名称")),
            "level": _header_index(headers, ("装束等级", "等级", "星级", "品质")),
            "series": _header_index(headers, ("系列", "套系")),
            "release_date": _header_index(headers, ("上线时间", "实装时间", "时间", "日期")),
            "acquisition": _header_index(headers, ("获取方式", "获取途径", "获得方式")),
            "image": _header_index(headers, ("立绘", "展示", "图片", "装束")),
        }
        if indices["character"] is None or indices["name"] is None:
            continue
        for row_number, cells in enumerate(rows, 1):
            char_cell = _cell(cells, indices["character"])
            name_cell = _cell(cells, indices["name"])
            character = _value(cells, indices["character"])
            name = _value(cells, indices["name"])
            char_page = _page_link(char_cell, ("装束", "皮肤", "图鉴"))
            skin_page = _page_link(name_cell, ("图鉴",))
            image_cell = _cell(cells, indices["image"]) or name_cell
            image = node_image(image_cell) if image_cell else None
            all_links = node_links(image_cell) if image_cell else []
            upload_placeholder = any(link["title"].startswith(("特殊:上传文件", "Special:Upload")) for link in all_links)
            if image_cell and not upload_placeholder:
                upload_placeholder = any(
                    "特殊:上传文件" in urllib.parse.unquote(anchor.attrs.get("href", ""))
                    or "Special:Upload" in urllib.parse.unquote(anchor.attrs.get("href", ""))
                    for anchor in image_cell.descendants("a")
                )
            if upload_placeholder:
                image = None
            item = {
                "character": character,
                "character_page": char_page,
                "name": name,
                "page": skin_page,
                "level": _value(cells, indices["level"]),
                "series": _value(cells, indices["series"]),
                "release_date": _value(cells, indices["release_date"]),
                "acquisition": _value(cells, indices["acquisition"]),
                "image": image,
                "metadata_only": image is None or upload_placeholder,
                "upload_placeholder": upload_placeholder,
                "source": {"page_title": page_title, "row": row_number},
            }
            item["missing_fields"] = _missing(item, ("character", "name", "level", "series", "release_date", "acquisition", "image"))
            if character and name:
                output.append(item)
            else:
                issues.append({"type": "skin_row_missing_identity", "source": item["source"], "text": " | ".join(cell.text() for cell in cells)})
    if not output:
        issues.append({"type": "no_skin_table_recognized", "page_title": page_title})
    issues.extend(unique_issues(output, ("character", "name", "page"), "skin"))
    return output, issues


def unique_issues(items: list[dict], fields: tuple[str, ...], kind: str) -> list[dict]:
    issues: list[dict] = []
    for field in fields:
        # A character can legitimately have many skins, so character is not a
        # uniqueness key for skin rows. Keep it available as a parsed field only.
        if kind == "skin" and field == "character":
            continue
        seen: dict[str, int] = {}
        for index, item in enumerate(items):
            value = item.get(field)
            if isinstance(value, dict):
                value = value.get("url") or value.get("title")
            if not value:
                continue
            key = clean_text(str(value)).casefold()
            if key in seen:
                issues.append({"type": f"duplicate_{kind}_{field}", "first_index": seen[key], "index": index, "value": value})
            else:
                seen[key] = index
    return issues


def find_catalog_links(source: str) -> dict[str, list[dict[str, str]]]:
    root = parse_html(source)
    result = {"characters": [], "skins": []}
    seen: set[str] = set()
    for link in node_links(root):
        title = link["title"]
        if not title or link["url"] in seen:
            continue
        target = None
        if "图鉴" in title and any(term in title for term in ("禁闭者", "角色")):
            target = "characters"
        elif "图鉴" in title and any(term in title for term in ("装束", "皮肤")):
            target = "skins"
        if target:
            seen.add(link["url"])
            result[target].append(link)
    return result


def parse_skin_detail(source: str) -> dict[str, object]:
    root = parse_html(source)
    result: dict[str, object] = {"introduction": None, "outfit_video": None, "dialogue_fields": {}}
    for table in root.descendants("table"):
        rows: list[list[Node]] = []
        for tr in table.descendants("tr"):
            cells = [child for child in tr.children if isinstance(child, Node) and child.tag in {"th", "td"}]
            if cells:
                rows.append(cells)
        for index, cells in enumerate(rows):
            header = cells[0].text() if cells and cells[0].tag == "th" else ""
            value_cell = None
            if len(cells) >= 2:
                value_cell = cells[1]
            elif header and index + 1 < len(rows) and rows[index + 1] and rows[index + 1][0].tag == "td":
                value_cell = rows[index + 1][0]
            value = value_cell.text() if value_cell else ""
            if header in {"资料", "装束资料", "装束介绍", "介绍"} and value:
                result["introduction"] = value
            elif "装束影像" in header and value_cell:
                external = next((a for a in value_cell.descendants("a") if a.attrs.get("href", "").startswith("https://www.bilibili.com/")), None)
                if external:
                    result["outfit_video"] = {"text": external.text(), "url": external.attrs["href"]}
            elif header and value and any(term in header for term in ("问候", "闲置", "烦躁", "对话", "语音", "触摸", "放置")):
                result["dialogue_fields"][header] = value
    return result


def attach_imageinfo(items: list[dict], imageinfo: dict[str, dict]) -> None:
    for item in items:
        image = item.get("image")
        if not isinstance(image, dict):
            continue
        title = image.get("file_title")
        info = imageinfo.get(str(title)) if title else None
        if info is None and isinstance(title, str):
            alternate = "File:" + title.split(":", 1)[-1]
            info = imageinfo.get(alternate)
        if info is None and image.get("current_url"):
            info = {"url": image["current_url"], "mime": None, "metadata_source": "rendered_html_only"}
        image["original"] = info or {"missing": True}
