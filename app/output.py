import csv
import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, List

from .models import Offer

CSV_FIELDS = [
    "offer_id", "section_id", "url", "text", "price", "price_display", "currency",
    "seller_id", "seller_name", "online", "auto_delivery", "promo",
    "subscription", "f_type", "f_extra", "matched_in", "full_description", "stock",
]


def _clip(value: str, width: int) -> str:
    value = " ".join(value.split())
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def print_console(offers: List[Offer], top: int) -> None:
    if top <= 0:
        top = len(offers)
    header = "%4s | %10s | %-16s | %-2s %-2s | %-6s | %-58s | %s" % (
        "#", "Цена", "Продавец", "Он", "Ав", "Plus", "Текст", "Ссылка")
    print(header)
    print("-" * len(header))
    for i, offer in enumerate(offers[:top], 1):
        if offer.f_type:
            plus = offer.f_type.capitalize()
        elif offer.subscription.lower() == "с подпиской":
            plus = "да"
        else:
            plus = ""
        print("%4d | %10s | %-16s | %-2s %-2s | %-6s | %-58s | %s" % (
            i,
            offer.price_display or "-",
            _clip(offer.seller_name, 16),
            "он" if offer.online else "--",
            "ав" if offer.auto_delivery else "--",
            _clip(plus, 6),
            _clip(offer.text, 58),
            offer.url,
        ))
    if len(offers) > top:
        print("... и ещё %d предложений (полный список — в файлах вывода)" % (len(offers) - top))


def save_json(offers: List[Offer], path: str, meta: Dict) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(offers),
        **meta,
        "offers": [offer.to_dict() for offer in offers],
    }
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def save_csv(offers: List[Offer], path: str) -> None:
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for offer in offers:
            row = offer.to_dict()
            row["f_extra"] = json.dumps(row["f_extra"], ensure_ascii=False) if row["f_extra"] else ""
            writer.writerow(row)


def save_sqlite(offers: List[Offer], path: str) -> None:
    _ensure_dir(path)
    now = datetime.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS offers (
                offer_id         TEXT PRIMARY KEY,
                section_id       TEXT,
                url              TEXT,
                text             TEXT,
                full_description TEXT,
                stock            TEXT,
                price            REAL,
                price_display    TEXT,
                currency         TEXT,
                seller_id        TEXT,
                seller_name      TEXT,
                online           INTEGER,
                auto_delivery    INTEGER,
                promo            INTEGER,
                subscription     TEXT,
                f_type           TEXT,
                f_extra          TEXT,
                matched_in       TEXT,
                updated_at       TEXT
            )
            """
        )
        # миграция старой базы без колонки f_extra
        columns = {row[1] for row in conn.execute("PRAGMA table_info(offers)")}
        if "f_extra" not in columns:
            conn.execute("ALTER TABLE offers ADD COLUMN f_extra TEXT")
        rows = []
        for offer in offers:
            d = offer.to_dict()
            rows.append((
                d["offer_id"], d["section_id"], d["url"], d["text"],
                d["full_description"], d["stock"],
                d["price"], d["price_display"], d["currency"],
                d["seller_id"], d["seller_name"],
                int(d["online"]), int(d["auto_delivery"]), int(d["promo"]),
                d["subscription"], d["f_type"],
                json.dumps(d["f_extra"], ensure_ascii=False) if d["f_extra"] else None,
                d["matched_in"], now,
            ))
        conn.executemany(
            """
            INSERT OR REPLACE INTO offers (
                offer_id, section_id, url, text, full_description, stock,
                price, price_display, currency, seller_id, seller_name,
                online, auto_delivery, promo, subscription, f_type, f_extra,
                matched_in, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
