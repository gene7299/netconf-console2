"""Exact YANG decimal64 validation, independent of floats/Decimal context."""
from dataclasses import dataclass
import re


class Decimal64Error(ValueError):
    pass


_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
_MIN, _MAX = -(2**63), 2**63 - 1


def format_scaled(value, digits):
    whole, fraction = divmod(abs(value), 10**digits)
    tail = str(fraction).zfill(digits).rstrip("0") or "0"
    return ("-" if value < 0 else "") + str(whole) + "." + tail


def _scaled(text, digits, label):
    if not isinstance(text, str) or _DECIMAL.fullmatch(text) is None:
        raise Decimal64Error(label + ": expected a decimal number without exponent or whitespace")
    negative = text.startswith("-")
    whole, _, fraction = text.lstrip("+-").partition(".")
    if len(fraction) > digits:
        raise Decimal64Error(label + ": decimal64 fraction-digits %d; at most %d fractional digits" % (digits, digits))
    whole = whole.lstrip("0") or "0"
    if len(whole) + digits > 20:
        raise Decimal64Error(label + ": exceeds the decimal64 signed 64-bit range")
    result = int(whole) * 10**digits + int(fraction.ljust(digits, "0") or "0")
    result = -result if negative else result
    if not _MIN <= result <= _MAX:
        raise Decimal64Error(label + ": exceeds the decimal64 signed 64-bit range")
    return result


@dataclass(frozen=True)
class Decimal64Rules:
    fraction_digits: int
    ranges: tuple[tuple[int, int], ...] = ((_MIN, _MAX),)

    def __post_init__(self):
        if type(self.fraction_digits) is not int or not 1 <= self.fraction_digits <= 18:
            raise Decimal64Error("decimal64 requires fraction-digits in 1..18")
        if not self.ranges or any(not _MIN <= lo <= hi <= _MAX for lo, hi in self.ranges):
            raise Decimal64Error("Invalid decimal64 range metadata")

    @property
    def range_text(self):
        return " | ".join(format_scaled(lo, self.fraction_digits) +
                          (".." + format_scaled(hi, self.fraction_digits) if lo != hi else "")
                          for lo, hi in self.ranges)

    def validate(self, text, label="value"):
        value = _scaled(text, self.fraction_digits, label)
        if not any(lo <= value <= hi for lo, hi in self.ranges):
            raise Decimal64Error(label + ": decimal64 range " + self.range_text)
        return value

    def constraints(self):
        return {"fraction-digits": str(self.fraction_digits), "range": self.range_text}

    @classmethod
    def from_constraints(cls, constraints):
        try:
            digits = int(constraints["fraction-digits"])
        except (KeyError, ValueError, TypeError):
            raise Decimal64Error("decimal64 fraction-digits metadata is missing or invalid") from None
        base = cls(digits)
        expression = constraints.get("range", "min..max")
        ranges = []
        for part in expression.split("|"):
            ends = part.strip().split("..")
            if len(ends) not in {1, 2}:
                raise Decimal64Error("Invalid decimal64 range expression")
            def bound(value):
                value = value.strip()
                return _MIN if value == "min" else _MAX if value == "max" else _scaled(value, digits, "range")
            ranges.append((bound(ends[0]), bound(ends[-1])))
        return cls(base.fraction_digits, tuple(ranges))


def decimal_rules(spec, visited=()):
    """Resolve leafrefs and intersect all inherited range restrictions."""
    if spec is None or id(spec) in visited:
        return None
    visited += (id(spec),)
    if getattr(spec, "name", "") == "leafref":
        target = getattr(spec, "i_target_node", None)
        typ = target.search_one("type") if target is not None else None
        return decimal_rules(getattr(typ, "i_type_spec", None), visited)
    if getattr(spec, "name", "") != "decimal64":
        return None
    digits = getattr(spec, "fraction_digits", None)
    if type(digits) is not int or not 1 <= digits <= 18:
        return None  # Unresolved/invalid compiled schema; never guess precision.
    base = decimal_rules(getattr(spec, "base", None), visited) or Decimal64Rules(digits)
    own_ranges = getattr(spec, "ranges", None)
    if own_ranges is None:
        return base
    def bound(value):
        if value == "min":
            return min(lo for lo, _ in base.ranges)
        if value == "max":
            return max(hi for _, hi in base.ranges)
        return value.value
    ranges = []
    for lo, hi in own_ranges:
        start = bound(lo)
        lo, hi = start, bound(hi) if hi is not None else start
        for base_lo, base_hi in base.ranges:
            start, stop = max(lo, base_lo), min(hi, base_hi)
            if start <= stop:
                ranges.append((start, stop))
    return Decimal64Rules(digits, tuple(ranges))


def decimal_members(spec, visited=()):
    """Expose decimal branches of unions without rejecting other member types."""
    if spec is None or id(spec) in visited:
        return []
    visited += (id(spec),)
    if getattr(spec, "name", "") == "union":
        return [rules for member in spec.types
                for rules in decimal_members(getattr(member, "i_type_spec", None), visited)]
    if getattr(spec, "name", "") == "leafref":
        target = getattr(spec, "i_target_node", None)
        typ = target.search_one("type") if target is not None else None
        return decimal_members(getattr(typ, "i_type_spec", None), visited)
    rules = decimal_rules(spec)
    return [rules] if rules else []
