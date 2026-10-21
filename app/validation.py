from datetime import datetime


def number(value, info):
    if isinstance(value, (str, bool)):
        raise ValueError("Ожидается число, а не строка или boolean")
    return value


def unique_products(items, info):
    if len({item.product_id for item in items}) != len(items):
        raise ValueError("product_id не должны повторяться")
    return items


def iso_datetime(value, info):
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("Ожидается строка с датой и временем ISO 8601")
    return datetime.fromisoformat(value)
