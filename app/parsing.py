import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from .models import Offer, Section

OFFER_ID_RE = re.compile(r"[?&]id=(\d+)")
SECTION_ID_RE = re.compile(r"lots/(\d+)")

try:
    import lxml  # noqa: F401

    _PARSER = "lxml"
except ImportError:  # pragma: no cover
    _PARSER = "html.parser"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, _PARSER)


def _clip(value: str) -> str:
    return " ".join(value.split())


def _strip_filter_suffix(text: str, f_values: List[str]) -> str:
    """Из текста предложения сайт добавляет значения фильтров: '..., С подпиской, Plus'."""
    if not f_values:
        return text
    suffix = ", " + ", ".join(f_values)
    if text.lower().endswith(suffix.lower()):
        return text[: -len(suffix)].rstrip()
    return text


def _parse_tc_item(item, section_id: str) -> Optional[Offer]:
    href = item.get("href", "")
    match = OFFER_ID_RE.search(href)
    if not match:
        return None

    text_el = item.select_one(".tc-desc-text")
    text = text_el.get_text(" ", strip=True) if text_el else ""

    price = None
    price_display = ""
    currency = ""
    price_el = item.select_one(".tc-price")
    if price_el is not None:
        raw = price_el.get("data-s")
        if raw:
            try:
                price = float(raw)
            except ValueError:
                pass
        price_display = _clip(price_el.get_text(" ", strip=True))
        unit = price_el.select_one(".unit")
        if unit is not None:
            currency = unit.get_text(strip=True)

    seller_el = item.select_one(".tc-user .media-user-name")

    f_extra = {}
    for attr, value in item.attrs.items():
        if not attr.startswith("data-f-") or not isinstance(value, str) or not value:
            continue
        key = attr[len("data-f-"):]
        if key == "subscription":
            continue
        if key == "type":
            continue
        f_extra[key] = value
    f_values = [item.get("data-f-subscription", "")] + list(f_extra.values()) + [item.get("data-f-type", "")]
    f_values = [v for v in f_values if v]
    text = _strip_filter_suffix(text, f_values)

    return Offer(
        offer_id=match.group(1),
        section_id=section_id,
        url=href if href.startswith("http") else "https://funpay.com" + href,
        text=text,
        price=price,
        price_display=price_display,
        currency=currency,
        seller_id=item.get("data-user", "") or "",
        seller_name=_clip(seller_el.get_text(" ", strip=True)) if seller_el else "",
        online=item.get("data-online") == "1",
        auto_delivery=item.get("data-auto") == "1",
        promo="offer-promo" in (item.get("class") or []),
        subscription=item.get("data-f-subscription", "") or "",
        f_type=item.get("data-f-type", "") or "",
        f_extra=f_extra,
    )


def parse_lots_page(html: str, section_id: str, url: str) -> Section:
    """Разбирает страницу раздела. Все предложения уже есть в HTML (в т.ч. lazyload-hidden)."""
    soup = _soup(html)
    section = Section(section_id=section_id, url=url)
    h1 = soup.find("h1")
    if h1 is not None:
        section.title = _clip(h1.get_text(" ", strip=True))

    seen = set()
    for item in soup.select("a.tc-item"):
        offer = _parse_tc_item(item, section_id)
        if offer is None or offer.offer_id in seen:
            continue  # промо-предложения дублируются в разметке
        seen.add(offer.offer_id)
        section.offers.append(offer)
    return section


def _param_value(block) -> str:
    label = block.find("h5")
    if label is None:
        return ""
    value_el = None
    for sibling in label.next_siblings:
        if getattr(sibling, "name", None):  # первый сосед-тег, а не текстовый узел
            value_el = sibling
            break
    if value_el is None:
        return ""
    for br in value_el.find_all("br"):
        br.replace_with("\n")
    return value_el.get_text().strip()


def parse_offer_page(html: str) -> Dict[str, str]:
    """Разбирает страницу предложения /lots/offer?id=<id>."""
    result = {
        "full_description": "",
        "stock": "",
        "seller_id": "",
        "seller_name": "",
        "online": "0",
    }
    soup = _soup(html)

    for block in soup.select("div.param-item"):
        label_el = block.find("h5")
        if label_el is None:
            continue
        label = label_el.get_text(strip=True)
        value = _param_value(block)
        if label == "Подробное описание":
            result["full_description"] = re.sub(r"\n{3,}", "\n\n", value)
        elif label == "Наличие":
            result["stock"] = _clip(value)

    chat = soup.select_one("div.chat[data-seller]")
    if chat is not None:
        result["seller_id"] = chat.get("data-seller", "") or ""
        name_el = chat.select_one(".media-user-name")
        if name_el is not None:
            result["seller_name"] = _clip(name_el.get_text(" ", strip=True))
        if chat.select_one(".media-user.online") is not None:
            result["online"] = "1"

    return result


def matches_filters(offer: Offer, filters: Dict[str, str]) -> bool:
    """Проверка предложения по фильтрам, которые задаёт сам FunPay для раздела.

    Ключи — id фильтров из разметки раздела ("subscription", "type" и прочие data-f-*),
    значения сравниваются без учёта регистра (у сайта "Plus" в форме и "plus" в data-атрибуте).
    """
    for key, expected in filters.items():
        if key == "subscription":
            actual = offer.subscription
        elif key == "type":
            actual = offer.f_type
        else:
            actual = offer.f_extra.get(key, "")
        if str(actual).lower() != str(expected).lower():
            return False
    return True


# постоянные тумблеры раздела FunPay: id input-а в форме -> подпись в конфиге
TOGGLES = [
    ("online", "online_only", "Только продавцы онлайн"),
    ("auto", "auto_delivery", "Автоматическая доставка"),
]


def discover_params(html: str) -> Dict:
    """Собирает параметры сортировки/фильтрации, которые FunPay предлагает на странице раздела:
    тумблеры (онлайн, автовыдача) и игровые фильтры формы (subscription, type, ...)."""
    soup = _soup(html)
    params = {"toggles": [], "groups": []}
    for input_name, config_key, label in TOGGLES:
        if soup.select_one('input[name="%s"]' % input_name):
            params["toggles"].append({"id": input_name, "config_key": config_key, "label": label})
    for group in soup.select(".form-group.lot-field[data-id]"):
        group_id = group.get("data-id")
        values = []
        for button in group.select("button[value]"):
            if button.get("value"):
                values.append({
                    "value": button.get("value"),
                    "label": _clip(button.get_text(" ", strip=True)),
                })
        for option in group.select("select option[value]"):
            if option.get("value"):
                values.append({
                    "value": option.get("value"),
                    "label": _clip(option.get_text(" ", strip=True)),
                })
        if group_id and values:
            params["groups"].append({"id": group_id, "values": values})
    return params
