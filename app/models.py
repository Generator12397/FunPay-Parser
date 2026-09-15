from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class Offer:
    """Одно предложение из таблицы раздела (a.tc-item)."""

    offer_id: str
    section_id: str
    url: str
    text: str

    price: Optional[float] = None
    price_display: str = ""
    currency: str = ""

    seller_id: str = ""
    seller_name: str = ""
    online: bool = False
    auto_delivery: bool = False
    promo: bool = False

    subscription: str = ""
    f_type: str = ""
    # остальные фильтры раздела (data-f-*), которые FunPay задаёт для конкретной игры
    f_extra: Dict[str, str] = field(default_factory=dict)

    # где нашлись ключевые слова: "any" (без проверки), "table", "description"
    matched_in: str = "any"
    full_description: str = ""
    stock: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Section:
    """Результат разбора страницы раздела /lots/<id>/."""

    section_id: str
    url: str
    title: str = ""
    offers: List[Offer] = field(default_factory=list)
