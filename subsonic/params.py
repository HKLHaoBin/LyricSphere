# Read GET query params. Repeated keys use getlist() semantics.

from typing import Any, List, Optional


def get_list(args, name: str) -> List[str]:
    if args is None:
        return []
    getter = getattr(args, 'getlist', None)
    if callable(getter):
        values = getter(name)
        return [str(v) for v in values if v is not None and str(v) != '']
    value = args.get(name)
    if value is None or value == '':
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v) != '']
    return [str(value)]


def get_one(args, *names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        values = get_list(args, name)
        if values:
            return values[0]
    return default


def get_int(args, name: str, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    raw = get_one(args, name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def get_bool(args, name: str, default: bool = False) -> bool:
    raw = get_one(args, name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')
