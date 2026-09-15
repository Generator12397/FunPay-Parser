import asyncio
import json
import os
import re
import sys
from typing import Dict, List, Tuple

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.client import FetchError, FunpayClient  # noqa: E402
from app.models import Offer  # noqa: E402
from app.output import print_console, save_csv, save_json, save_sqlite  # noqa: E402
from app.parsing import matches_filters, parse_lots_page, parse_offer_page  # noqa: E402

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

SECTION_ID_RE = re.compile(r"lots/(\d+)")

DEFAULT_CONFIG = {
    "sections": ["https://funpay.com/lots/1355/"],
    "keywords": ["chatgpt", "личный"],
    "sort": "price",
    "max_price": None,
    "online_only": False,
    "auto_delivery": False,
    "filters": {},
    "concurrency": 5,
    "delay": 0.0,
    "top": 50,
    "max_description_checks": 0,
    "output": {
        "console": True,
        "json": "results/result.json",
        "csv": "results/result.csv",
        "sqlite": "results/results.db",
    },
}


def _setup_stdio() -> None:
    # вывод с эмодзи не должен падать при перенаправлении в файл/пайп
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_config() -> Dict:
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as file:
            json.dump(DEFAULT_CONFIG, file, ensure_ascii=False, indent=2)
        print("Создан config.json с параметрами по умолчанию — заполните его и запустите снова.")
        sys.exit(0)
    with open(CONFIG_PATH, encoding="utf-8") as file:
        config = json.load(file)
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    merged["output"] = {**DEFAULT_CONFIG["output"], **(config.get("output") or {})}
    return merged


def normalize_section(raw: str) -> Tuple[str, str]:
    raw = str(raw).strip()
    match = SECTION_ID_RE.search(raw)
    if match:
        section_id = match.group(1)
    elif raw.isdigit():
        section_id = raw
    else:
        raise ValueError("Не удалось разобрать адрес раздела: %r" % raw)
    return section_id, "https://funpay.com/lots/%s/" % section_id


def matches_all(keywords: List[str], *texts: str) -> bool:
    haystack = " ".join(texts).lower()
    return all(kw in haystack for kw in keywords)


def sort_offers(offers: List[Offer], mode: str) -> None:
    mode = (mode or "price").lower()
    if mode == "price":
        offers.sort(key=lambda o: (o.price is None, o.price or 0.0))
    elif mode in ("price-desc", "price_desc"):
        offers.sort(key=lambda o: (o.price is None, -(o.price or 0.0)))


async def apply_keywords(client: FunpayClient, offers: List[Offer],
                         keywords: List[str], max_checks: int) -> Tuple[Dict, List[Offer]]:
    """Быстрый путь: все слова в тексте из таблицы. Иначе догружаем страницу предложения
    и ищем в подробном описании. Возвращает (статистика, подходящие предложения)."""
    if not keywords:
        for offer in offers:
            offer.matched_in = "any"
        stats = {"table": len(offers), "description": 0, "checked": 0, "failed": 0, "skipped": 0}
        return stats, list(offers)

    table_matched: List[Offer] = []
    to_check: List[Offer] = []
    for offer in offers:
        if matches_all(keywords, offer.text):
            offer.matched_in = "table"
            table_matched.append(offer)
        else:
            to_check.append(offer)

    # проверять начинаем с самых дешёвых
    to_check.sort(key=lambda o: (o.price is None, o.price or 0.0))
    skipped = 0
    if max_checks and len(to_check) > max_checks:
        skipped = len(to_check) - max_checks
        to_check = to_check[:max_checks]

    total = len(to_check)
    progress = {"checked": 0, "failed": 0}

    async def check(offer: Offer) -> Offer:
        try:
            html = await client.get(offer.url)
        except FetchError:
            progress["failed"] += 1
            return None
        info = parse_offer_page(html)
        offer.full_description = info["full_description"]
        offer.stock = info["stock"]
        progress["checked"] += 1
        if progress["checked"] % 25 == 0 or progress["checked"] == total:
            print("  Проверено описаний: %d/%d" % (progress["checked"], total))
        if matches_all(keywords, offer.text, offer.full_description):
            offer.matched_in = "description"
            return offer
        return None

    checked = await asyncio.gather(*(check(offer) for offer in to_check))
    description_matched = [offer for offer in checked if offer is not None]

    return {
        "table": len(table_matched),
        "description": len(description_matched),
        "checked": progress["checked"],
        "failed": progress["failed"],
        "skipped": skipped,
    }, table_matched + description_matched


async def run(config: Dict) -> None:
    keywords = [str(kw).strip().lower() for kw in (config.get("keywords") or []) if str(kw).strip()]
    if not config.get("sections"):
        print("В config.json не задан ни один раздел (sections).")
        sys.exit(1)
    sections = [normalize_section(raw) for raw in config["sections"]]
    sort_mode = config.get("sort", "price")
    max_checks = int(config.get("max_description_checks") or 0)
    max_price = None
    if config.get("max_price") not in (None, "", 0):
        try:
            max_price = float(config["max_price"])
        except (TypeError, ValueError):
            print("Неверное значение max_price=%r — параметр проигнорирован." % config.get("max_price"))

    results: List[Offer] = []
    async with FunpayClient(concurrency=config.get("concurrency", 5),
                            delay=config.get("delay", 0.0)) as client:
        for section_id, url in sections:
            print("Загрузка раздела %s ..." % url)
            html = await client.get(url)
            section = parse_lots_page(html, section_id, url)
            offers = section.offers
            print("Раздел: %s — %d предложений" % (section.title or url, len(offers)))

            if config.get("online_only"):
                offers = [offer for offer in offers if offer.online]
                print("Фильтр «Только продавцы онлайн»: осталось %d" % len(offers))
            if config.get("auto_delivery"):
                offers = [offer for offer in offers if offer.auto_delivery]
                print("Фильтр «Автоматическая доставка»: осталось %d" % len(offers))

            filters = config.get("filters") or {}
            if filters:
                pretty = ", ".join("%s=%s" % (key, value) for key, value in filters.items())
                offers = [offer for offer in offers if matches_filters(offer, filters)]
                print("Фильтры FunPay (%s): осталось %d" % (pretty, len(offers)))

            if max_price is not None:
                before = len(offers)
                offers = [offer for offer in offers if offer.price is None or offer.price <= max_price]
                print("Ограничение цены <= %s: осталось %d (отсечено %d, парсинг по ним прекращён)" % (
                    max_price, len(offers), before - len(offers)))

            stats, valid = await apply_keywords(client, offers, keywords, max_checks)
            sort_offers(valid, sort_mode)
            results.extend(valid)

            summary = "Совпало: %d (в таблице: %d, в описаниях: %d)" % (
                len(valid), stats["table"], stats["description"])
            details = []
            if stats["checked"]:
                details.append("проверено описаний: %d" % stats["checked"])
            if stats["failed"]:
                details.append("не удалось загрузить: %d" % stats["failed"])
            if stats["skipped"]:
                details.append("пропущено по лимиту: %d" % stats["skipped"])
            if details:
                summary += " [" + ", ".join(details) + "]"
            print(summary)

    sort_offers(results, sort_mode)
    print("\nИтого подходящих предложений: %d" % len(results))

    if config.get("output", {}).get("console", True):
        print()
        print_console(results, int(config.get("top") or 50))

    meta = {
        "sections": [url for _, url in sections],
        "keywords": keywords,
        "sort": sort_mode,
        "max_price": max_price,
        "online_only": bool(config.get("online_only")),
        "auto_delivery": bool(config.get("auto_delivery")),
        "filters": config.get("filters") or {},
    }
    output = config.get("output", {})
    saves = [
        ("json", save_json, (results, output.get("json"), meta)),
        ("csv", save_csv, (results, output.get("csv"))),
        ("sqlite", save_sqlite, (results, output.get("sqlite"))),
    ]
    for name, saver, args in saves:
        path = output.get(name)
        if path:
            try:
                saver(*args)
                print("Сохранено (%s): %s" % (name, path))
            except (OSError, json.JSONDecodeError) as exc:
                print("Не удалось сохранить %s (%s): %s" % (name, path, exc))


def main() -> None:
    _setup_stdio()
    config = load_config()
    try:
        asyncio.run(run(config))
    except ValueError as exc:
        print(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
