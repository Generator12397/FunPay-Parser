"""Пред-парс: показывает параметры сортировки/фильтрации, которые FunPay предлагает
на странице раздела, и записывает выбранные в config.json.

Запуск: python preparse.py
"""
import asyncio
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.client import FetchError, FunpayClient  # noqa: E402
from app.main import CONFIG_PATH, DEFAULT_CONFIG, load_config, normalize_section  # noqa: E402
from app.parsing import discover_params  # noqa: E402


def _setup_stdio() -> None:
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


def choose(prompt: str, options) -> int:
    """Спрашивает номер варианта. Enter — пропустить (возвращает None)."""
    print(prompt)
    for i, label in enumerate(options, 1):
        print("  %d) %s" % (i, label))
    while True:
        raw = ask("Номер (Enter — пропустить): ")
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print("Неверный ввод, попробуйте ещё раз.")


def pick_section(config: dict) -> str:
    sections = [str(s) for s in (config.get("sections") or []) if str(s).strip()]
    if not sections:
        while True:
            raw = ask("В config.json нет разделов. Введите адрес раздела FunPay "
                      "(например https://funpay.com/lots/1355/ или просто 1355): ")
            try:
                _, url = normalize_section(raw)
                return url
            except ValueError as exc:
                print(exc)
    if len(sections) == 1:
        return sections[0]
    idx = choose("Для какого раздела найти параметры?", sections)
    return sections[idx if idx is not None else 0]


def yes_no(prompt: str) -> bool:
    return ask(prompt + " (y/n, Enter — n): ").lower() in ("y", "yes", "д", "да", "1")


async def run() -> None:
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as file:
            json.dump(DEFAULT_CONFIG, file, ensure_ascii=False, indent=2)
        print("Создан config.json с параметрами по умолчанию.")

    config = load_config()
    _, url = normalize_section(pick_section(config))
    print("Загрузка %s ..." % url)
    try:
        async with FunpayClient() as client:
            html = await client.get(url)
    except FetchError as exc:
        print("Не удалось загрузить раздел: %s" % exc)
        sys.exit(1)

    params = discover_params(html)
    if not params["toggles"] and not params["groups"]:
        print("FunPay не предлагает для этого раздела ни одного параметра фильтрации.")
        return

    print("\nНайдены параметры раздела (задаёт сам FunPay):")
    changed = False

    for toggle in params["toggles"]:
        current = bool(config.get(toggle["config_key"]))
        print("\nТумблер «%s» (сейчас: %s)" % (toggle["label"], "вкл" if current else "выкл"))
        if not current and yes_no("Включить в конфиг?"):
            config[toggle["config_key"]] = True
            changed = True
            print("→ %s: true" % toggle["config_key"])

    filters = config.get("filters") or {}
    for group in params["groups"]:
        current = filters.get(group["id"])
        labels = [value["label"] for value in group["values"]]
        print("\nФильтр «%s» (сейчас: %s)" % (group["id"], current or "не задан"))
        idx = choose("Возможные значения:", labels)
        if idx is not None:
            config.setdefault("filters", {})[group["id"]] = group["values"][idx]["value"]
            changed = True
            print("→ filters.%s = %s" % (group["id"], group["values"][idx]["value"]))

    if not changed:
        print("\nКонфиг не изменён.")
        return

    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
    print("\nСохранено в config.json:")
    print("  online_only: %s" % config.get("online_only", False))
    print("  auto_delivery: %s" % config.get("auto_delivery", False))
    print("  filters: %s" % json.dumps(config.get("filters") or {}, ensure_ascii=False))
    print("Теперь можно запускать основной парсер: python -m app.main")


def main() -> None:
    _setup_stdio()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nПрервано.")


if __name__ == "__main__":
    main()
