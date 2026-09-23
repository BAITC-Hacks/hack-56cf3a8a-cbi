"""Deterministic contractor matching for the #79-lite challenge."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


def _split(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split("|") if x.strip()]


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "да"}


@dataclass(frozen=True)
class Request:
    city: str
    date: str
    event_format: str
    category: str
    budget_kzt: int
    duration_hours: float | None = None
    language: str | None = None
    wishes: str = ""


@dataclass(frozen=True)
class Contractor:
    id: str
    anon_name: str
    categories: str
    city: str
    city_imputed: bool
    synthetic: bool
    price_from_kzt: int
    price_imputed: bool
    event_formats: str
    languages: str
    max_hours: float | None
    busy_dates: str
    description: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Contractor":
        def number(name: str) -> int | None:
            value = str(row.get(name, "")).strip()
            return int(float(value)) if value else None

        def hours() -> float | None:
            value = str(row.get("max_hours", "")).strip()
            return float(value) if value else None

        return cls(
            id=str(row.get("id", "")).strip(), anon_name=str(row.get("anon_name", "")).strip(),
            categories=str(row.get("categories", "")).strip(), city=str(row.get("city", "")).strip(),
            city_imputed=_bool(row.get("city_imputed")), synthetic=_bool(row.get("synthetic")),
            price_from_kzt=number("price_from_kzt") or 0, price_imputed=_bool(row.get("price_imputed")),
            event_formats=str(row.get("event_formats", "")).strip(), languages=str(row.get("languages", "")).strip(),
            max_hours=hours(), busy_dates=str(row.get("busy_dates", "")).strip(),
            description=str(row.get("description", "")).strip(),
        )


@dataclass
class MatchResult:
    status: str
    candidates_count: int
    matches: list[dict[str, Any]]
    excluded: dict[str, list[str]]
    message: str


def load_catalog(path: str | Path) -> list[Contractor]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return [Contractor.from_row(row) for row in csv.DictReader(handle)]


def hard_filter(request: Request, catalog: Iterable[Contractor]):
    city_matches = [c for c in catalog if c.city == request.city]
    by_category = [c for c in city_matches if request.category in _split(c.categories)]
    excluded = {key: [] for key in ("busy", "budget", "format", "duration", "language")}
    passed: list[Contractor] = []
    for candidate in by_category:
        failures: list[str] = []
        if request.date in _split(candidate.busy_dates): failures.append("busy")
        if candidate.price_from_kzt > request.budget_kzt: failures.append("budget")
        if request.event_format not in _split(candidate.event_formats): failures.append("format")
        if request.duration_hours and candidate.max_hours and candidate.max_hours < request.duration_hours: failures.append("duration")
        if request.language and request.language not in _split(candidate.languages): failures.append("language")
        if failures:
            for reason in failures: excluded[reason].append(candidate.id)
        else:
            passed.append(candidate)
    return by_category, passed, excluded


_TOKEN = re.compile(r"[\wа-яё]+", re.IGNORECASE)


def _similarity(candidate: Contractor, request: Request) -> float:
    """Stable local semantic proxy; replace with cached API embeddings when configured."""
    left = set(_TOKEN.findall(candidate.description.lower()))
    right = set(_TOKEN.findall(f"{request.category} {request.event_format} {request.wishes}".lower()))
    return len(left & right) / math.sqrt(max(1, len(left) * len(right)))


def rank(request: Request, candidates: Iterable[Contractor]) -> list[tuple[Contractor, float, list[str]]]:
    ranked = []
    for c in candidates:
        criteria = []
        score = _similarity(c, request)
        if request.language and request.language in _split(c.languages): score += 1; criteria.append("language")
        if request.duration_hours and c.max_hours and c.max_hours >= request.duration_hours: score += 1; criteria.append("duration")
        ranked.append((c, score, criteria))
    return sorted(ranked, key=lambda item: (-item[1], item[0].price_from_kzt, item[0].id))


def _empty_message(request: Request, count: int, excluded: dict[str, list[str]]) -> str:
    if count == 0:
        return f'В городе {request.city} нет подрядчиков категории «{request.category}».'
    parts = []
    labels = {"busy": f"заняты на {request.date}", "budget": f"не укладываются в бюджет {request.budget_kzt:,} ₸".replace(",", " "), "format": f"не берут формат «{request.event_format}»"}
    if request.duration_hours:
        labels["duration"] = f"не работают {request.duration_hours:g} ч."
    if request.language:
        labels["language"] = f"не работают на языке «{request.language}»"
    for key, label in labels.items():
        if excluded[key]: parts.append(f"{len(excluded[key])} {label}")
    return f"По категории «{request.category}» в городе {request.city} найдено {count} подрядчиков, но ни один не подошёл: " + ", ".join(parts) + "."


def match(request: Request, catalog: Iterable[Contractor], top_n: int = 3) -> MatchResult:
    by_category, passed, excluded = hard_filter(request, catalog)
    if not by_category:
        return MatchResult("no_category", 0, [], excluded, _empty_message(request, 0, excluded))
    if not passed:
        return MatchResult("no_match", len(by_category), [], excluded, _empty_message(request, len(by_category), excluded))
    matches = []
    for candidate, score, criteria in rank(request, passed)[:top_n]:
        item = asdict(candidate)
        item.update(score=round(score, 8), matched_criteria=criteria)
        matches.append(item)
    return MatchResult("matched", len(by_category), matches, excluded, f"Подобрано {len(matches)} подрядчика.")


def _fallback_reason(candidate: dict[str, Any], request: Request) -> str:
    criteria = candidate.get("matched_criteria") or []
    extras = []
    if "language" in criteria:
        extras.append(f"работает на языке «{request.language}»")
    if "duration" in criteria:
        extras.append(f"держит нужную длительность ({request.duration_hours:g} ч)")
    snippet = str(candidate.get("description", "")).strip()
    snippet = re.split(r"(?<=[.!?])\s+", snippet)[0][:180].rstrip(".!?")
    detail = f" В описании указано: {snippet}" if snippet else ""
    extra_text = f"; также {', '.join(extras)}." if extras else "."
    price = f"{candidate['price_from_kzt']:,}".replace(",", " ")
    budget = f"{request.budget_kzt:,}".replace(",", " ")
    return (
        f"{candidate['anon_name']} — категория «{request.category}», "
        f"стартовая цена {price} ₸ в пределах бюджета {budget} ₸."
        f"{detail}{extra_text}"
    )


def explain(candidate: dict[str, Any], request: Request, cache_dir: str | Path = ".cache/explanations") -> dict[str, str]:
    """Generate one factual explanation with optional OpenAI API; cache by request+candidate."""
    payload = json.dumps({"request": asdict(request), "candidate": candidate}, ensure_ascii=False, sort_keys=True)
    key = hashlib.sha256(payload.encode()).hexdigest()
    cache = Path(cache_dir); cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{key}.json"
    if target.exists(): return json.loads(target.read_text(encoding="utf-8"))
    result: dict[str, str] | None = None
    used_api = False

    if os.getenv("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            prompt = (
                'Напиши 1-2 предложения на русском в JSON {"reason":"string"} только по этим фактам. '
                "Не выдумывай. REQUEST=" + json.dumps(asdict(request), ensure_ascii=False)
                + " CANDIDATE=" + json.dumps(candidate, ensure_ascii=False)
            )
            raw = OpenAI().responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), input=prompt, temperature=0
            ).output_text
            clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(clean)
            if isinstance(parsed, dict) and isinstance(parsed.get("reason"), str) and parsed["reason"].strip():
                result = {"reason": parsed["reason"].strip()}
                used_api = True
        except Exception:
            result = None

    if result is None:
        result = {"reason": _fallback_reason(candidate, request)}

    # Do not persist fallback results: a later run can retry the API after recovery.
    if used_api:
        target.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result
