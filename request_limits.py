"""Upper distance bounds must come from the current human request, never tool guesses."""
import re

FIELDS = ("max_total_ly", "max_loaded_ly", "max_approach_ls")
DISTANCE = re.compile(r"(?<![\w.])(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
                      r"(?P<unit>light[ -]?years?|ly|light[ -]?seconds?|ls)\b", re.I)
UPPER = re.compile(r"\b(within|under|below|less than|at most|up to|no more than|maximum|max|limit|cap)\b", re.I)
LOADED = re.compile(r"\bloaded\b|mine.{0,25}(?:buyer|sell)|mining.{0,25}selling", re.I)


def request_bounds(text):
    bounds = dict.fromkeys(FIELDS)
    for match in DISTANCE.finditer(text):
        prefix = text[:match.start()]
        clause = re.split(r"[,;!?]|\band\b", prefix, flags=re.I)[-1][-100:]
        suffix = text[match.end():match.end()+35]
        # "No 50 LY limit" or "ignore the 50 LY cap" is not an upper bound.
        if re.search(r"\b(?:ignore|remove|without)\b", clause, re.I) or re.search(r"\bno\s*$", clause, re.I):
            continue
        if not (UPPER.search(clause) or re.match(r"\s*(?:maximum|max|limit|cap)\b", suffix, re.I)):
            continue
        if re.search(r"\b(?:at least|more than|over)\s*$", clause, re.I):
            continue
        value = float(match["number"].replace(",", ""))
        unit = re.sub(r"[ -]", "", match["unit"].lower())
        if unit in ("ls", "lightsecond", "lightseconds"):
            field = "max_approach_ls"
        else:
            field = "max_loaded_ly" if LOADED.search(clause) else "max_total_ly"
        if value <= 0 or value > (100000000 if field == "max_approach_ls" else 100000):
            raise ValueError("Explicit distance bound is outside the supported range")
        bounds[field] = value if bounds[field] is None else min(value, bounds[field])
    # Avoid silently dropping a spoken-number cap we cannot safely parse.
    if re.search(
            r"\b(?:within|under|at most|no more than)\s+(?:[a-z-]+\s+){1,6}(?:light[ -]?years?|light[ -]?seconds?)\b", text, re.I):
        raise ValueError("An explicit distance limit was spoken but could not be parsed safely; use numeric digits and LY/LS")
    return bounds


def ground_limits(args, current_request):
    if current_request is None:
        if any(getattr(args, field) is not None for field in FIELDS):
            raise ValueError("Cannot verify hard distance limits without the current Commander request")
        return args
    # Also recover human bounds if the model omitted them. Never carry previous-turn bounds.
    return args.model_copy(update=request_bounds(current_request))
