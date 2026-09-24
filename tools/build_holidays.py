"""
Build ``holidays.json`` from the State Council (gov.cn) announcements.

DeepSeek's peak/off-peak rule excludes "Chinese public holidays", so the widget
needs the real dates.  Nothing is guessed here: the raw year files come from the
`holiday-cn <https://github.com/NateScarlet/holiday-cn>`_ dataset, which is
generated from the State Council papers and keeps the paper URL for each year.

    python tools/build_holidays.py            # uses tmp/<year>.json
    python tools/build_holidays.py --fetch     # downloads the years first

Only ``isOffDay: true`` entries are kept — the make-up working weekends
(调休) are *not* public holidays, and DeepSeek does not exclude them.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "tmp"
OUTPUT = ROOT / "holidays.json"
RAW_URL = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"

# English glosses so the widget can label a holiday without a CJK font dependency.
ENGLISH = {
    "元旦": "New Year's Day",
    "春节": "Spring Festival",
    "清明节": "Qingming Festival",
    "劳动节": "Labour Day",
    "端午节": "Dragon Boat Festival",
    "中秋节": "Mid-Autumn Festival",
    "国庆节": "National Day",
    "国庆节、中秋节": "National Day & Mid-Autumn Festival",
}


def fetch(year: int) -> dict | None:
    url = RAW_URL.format(year=year)
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        print(f"  {year}: not available ({error})")
        return None
    (CACHE / f"{year}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  {year}: downloaded")
    return payload


def read_cached(year: int) -> dict | None:
    path = CACHE / f"{year}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def collect(payload: dict) -> tuple[dict[str, dict[str, str]], list[str]]:
    days: dict[str, dict[str, str]] = {}
    papers: list[str] = []
    for entry in payload.get("days") or []:
        if not entry.get("isOffDay"):
            continue  # make-up working weekend, not a public holiday
        stamp = str(entry.get("date") or "")
        if not stamp:
            continue
        try:
            date.fromisoformat(stamp)
        except ValueError:
            continue
        name = str(entry.get("name") or "").strip()
        days[stamp] = {"name": name, "en": ENGLISH.get(name, "")}
    for paper in payload.get("papers") or []:
        if paper not in papers:
            papers.append(str(paper))
    return days, papers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=[date.today().year - 1, date.today().year, date.today().year + 1],
    )
    parser.add_argument("--fetch", action="store_true", help="download from GitHub first")
    args = parser.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    days: dict[str, dict[str, str]] = {}
    papers: list[str] = []

    for year in args.years:
        payload = fetch(year) if args.fetch else read_cached(year)
        if payload is None and not args.fetch:
            payload = fetch(year)
        if payload is None:
            continue
        found, paper_urls = collect(payload)
        if not found:
            print(f"  {year}: no holiday days published yet")
            continue
        days.update(found)
        papers.extend(url for url in paper_urls if url not in papers)
        print(f"  {year}: {len(found)} public holiday days")

    if not days:
        print("no holiday data available — nothing written", file=sys.stderr)
        return 1

    OUTPUT.write_text(
        json.dumps(
            {
                "note": (
                    "Chinese public holidays (isOffDay entries only) used to exclude peak "
                    "hours. Peak = 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri, excluding "
                    "these dates; everything else is off-peak."
                ),
                "source": "https://github.com/NateScarlet/holiday-cn",
                "source_note": "generated from the State Council (gov.cn) announcements",
                "papers": papers,
                "generated": date.today().isoformat(),
                "years": sorted({stamp[:4] for stamp in days}),
                "days": dict(sorted(days.items())),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT} ({len(days)} days, years {', '.join(sorted({s[:4] for s in days}))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
