"""Verify data/i18n/adopted.en.json: every Chinese adoption string shown on the
website has exactly one English entry; entries carry identical URLs, filenames
and numbers; English text is CJK-free. --sync rewrites the file (adds missing
entries with empty en, drops orphans) then still runs the checks.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
I18N = ROOT / 'data' / 'i18n' / 'adopted.en.json'

CJK = re.compile(r'[㐀-鿿]')
# CJK + fullwidth/CJK punctuation forbidden in English output
EN_BAD = re.compile(r'[㐀-鿿　-〿＀-￯]')
URL_RE = re.compile(r'https?://[^\s<>"\'\u3000-\u9fff\uff00-\uffef]+')
URL_TAIL = re.compile(r'[;,.]+$')  # trim trailing ASCII sentence punctuation captured inside URLs
FILE_RE = re.compile(r'[A-Za-z0-9_.-]+\.(?:json|png|jpe?g|webp|csv|md|txt)')  # JS \w semantics
NUM_RE = re.compile(r'[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?')
NUM_UNIT = re.compile(r'\s*([BKM])\b')


def required_strings() -> list[str]:
    """Ordered, de-duplicated: adopted.csv (row order, source then decision_note),
    then benchmark-configurations source, then benchmark-points source."""
    out, seen = [], set()

    def add(s: str | None) -> None:
        if s and CJK.search(s) and s not in seen:
            seen.add(s)
            out.append(s)

    with (ROOT / 'data' / 'adopted.csv').open(encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            add(r['source'])
            add(r['decision_note'])
    for name in ('benchmark-configurations.json', 'benchmark-points.json'):
        for r in json.loads((ROOT / 'derived' / name).read_text(encoding='utf-8')):
            add(r.get('source'))
    return out


def urls(text: str) -> list[str]:
    return sorted(URL_TAIL.sub('', m.group(0)) for m in URL_RE.finditer(text))


def files(text: str) -> list[str]:
    return sorted(FILE_RE.findall(text))


# Separators allowed inside a shared-unit run of numbers (a run ends in 亿/万
# and marks every member with that unit). ASCII comma excluded — it is the
# thousands separator, not a list separator.
RUN_SEP = r'[~～–\-→、/，和与至]'
NUM_TOK = r'[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?'
RUN_RE = re.compile(NUM_TOK + r'(?:\s*' + RUN_SEP + r'\s*' + NUM_TOK + r')*\s*([亿万])')


def zh_number_items(text: str) -> list[tuple[Decimal, str]]:
    """zh number tokens with unit tags: 'yi' if followed by optional space + 亿
    or a member of a separator-joined run ending in 亿 (e.g. 8.43~8.99亿,
    27.62→25.89亿,
    24.60/28.96 亿, 8.53、9.1、10.3亿/周 tag every member); 'wan' likewise for
    万; '' otherwise."""
    stripped = URL_RE.sub(' ', FILE_RE.sub(' ', text))
    runs = [(m.span(), 'yi' if m.group(1) == '亿' else 'wan')
            for m in RUN_RE.finditer(stripped)]
    items = []
    for m in NUM_RE.finditer(stripped):
        tag = next((t for (a, b), t in runs if a <= m.start() < b), '')
        items.append((Decimal(m.group(0).replace(',', '')), tag))
    return items


def check_numbers(zh: str, en: str) -> list[str]:
    """zh token multiset must equal en multiset with unit fidelity enforced:
    a yi-tagged zh y matches ONLY an en x followed by B with x == y/10, or an
    en x followed by M with x == y*100 (sub-billion 亿, zh 0.62亿 → en 62M);
    a wan-tagged y matches ONLY en x followed by K with x == y*10;
    an untagged y matches en x == y (any suffix or none)."""
    zh_vals = zh_number_items(zh)
    stripped_en = URL_RE.sub(' ', FILE_RE.sub(' ', en))
    problems = []
    items = []  # (raw_text, value, unit)
    for m in NUM_RE.finditer(stripped_en):
        raw = Decimal(m.group(0).replace(',', ''))
        unit = NUM_UNIT.match(stripped_en, m.end())
        items.append((m.group(0), raw, unit.group(1) if unit else ''))
    # bipartite match (Kuhn): en tokens may legitimately alias between raw and
    # unit-scaled values (e.g. zh '20×' vs en '20B' → 200); matching avoids a
    # greedy raw match stealing a token a later unit-scaled token needs.
    adj = [[] for _ in items]
    for i, (_, x, u) in enumerate(items):
        seen = set()
        for j, (y, tag) in enumerate(zh_vals):
            ok = ((x == y) if not tag else
                  ((u == 'B' and x == y / 10) or (u == 'M' and x == y * 100)) if tag == 'yi'
                  else (u == 'K' and x == y * 10))
            if ok and j not in seen:
                adj[i].append(j)
                seen.add(j)
    match = [-1] * len(zh_vals)

    def aug(i, vis):
        for j in adj[i]:
            if not vis[j]:
                vis[j] = True
                if match[j] == -1 or aug(match[j], vis):
                    match[j] = i
                    return True
        return False

    matched = 0
    for i in sorted(range(len(items)), key=lambda i: len(adj[i])):
        if aug(i, [False] * len(zh_vals)):
            matched += 1
    if matched != len(items) or len(zh_vals) != len(items):
        problems.append(f'zh nums {sorted(str(v) for v, _ in zh_vals)} vs en {[t for t, _, _ in items]}')
    return problems


SELFTEST = [
    ('0.62亿', '62M', True),
    ('0.62亿', '0.62M', False),
    ('0.62亿', '0.062B', True),
    ('301.7亿', '30.17B', True),
    ('301.7亿', '301.7B', False),
    ('8.43~8.99亿', '843M~899M', True),
    ('5万', '50K', True),
    ('45.69M', '45.69M', True),
]


def selftest() -> list[str]:
    return [f'{zh!r} vs {en!r}: expected {"match" if ok else "mismatch"}'
            for zh, en, ok in SELFTEST if bool(check_numbers(zh, en)) != (not ok)]


def load_file() -> dict:
    return json.loads(I18N.read_text(encoding='utf-8'))


def main() -> None:
    st = selftest()
    if st:
        print('SELFTEST FAIL:')
        for f in st:
            print(' ', f)
        sys.exit(1)
    sync = '--sync' in sys.argv
    req = required_strings()
    doc = load_file() if I18N.is_file() else {'_doc': '', 'keepCJK': [], 'entries': []}
    keep = doc.get('keepCJK', [])

    if sync:
        have = {e['zh'] for e in doc['entries']}
        entries = [{'zh': z, 'en': next((e['en'] for e in doc['entries'] if e['zh'] == z), '')}
                   for z in req]
        # keep any orphan-free original order preserved by req order; write back
        doc['entries'] = entries
        doc.setdefault('_doc', '')
        doc.setdefault('keepCJK', [])
        I18N.parent.mkdir(parents=True, exist_ok=True)
        I18N.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'sync: {len(entries)} entries written ({sum(1 for e in entries if not e["en"])} empty)')

    doc = load_file()
    failures = []
    by_zh: dict[str, list[dict]] = {}
    for e in doc['entries']:
        by_zh.setdefault(e['zh'], []).append(e)

    for z in req:
        es = by_zh.get(z, [])
        if not es:
            failures.append(f'missing entry: {z[:80]}')
        elif len(es) > 1:
            failures.append(f'duplicate entry ({len(es)}x): {z[:80]}')
        elif not es[0].get('en'):
            failures.append(f'empty en: {z[:80]}')
    for z, es in by_zh.items():
        if z not in req:
            failures.append(f'orphan entry: {z[:80]}')

    for e in doc['entries']:
        z, en = e['zh'], e.get('en', '')
        if not en:
            continue
        en_stripped = en
        for k in keep:
            if k in en_stripped:
                if k not in z:
                    failures.append(f'keepCJK {k!r} used in en but absent from zh: {z[:60]}')
                en_stripped = en_stripped.replace(k, '')
        bad = EN_BAD.search(en_stripped)
        if bad:
            failures.append(f'CJK/fullwidth {bad.group(0)!r} in en: {z[:60]}')
        if urls(z) != urls(en):
            failures.append(f'URL mismatch: {z[:60]}\n    zh={urls(z)}\n    en={urls(en)}')
        if files(z) != files(en):
            failures.append(f'filename mismatch: {z[:60]}\n    zh={files(z)}\n    en={files(en)}')
        for p in check_numbers(z, en):
            failures.append(f'number mismatch: {z[:60]}: {p}')

    if failures:
        print(f'FAIL: {len(failures)} problem(s)')
        for f in failures[:80]:
            print('  ' + f)
        if len(failures) > 80:
            print(f'  ... and {len(failures) - 80} more')
        sys.exit(1)
    print(f'PASS: {len(req)} required strings; {len(doc["entries"])} entries; '
          f'{len(keep)} keepCJK exceptions')


if __name__ == '__main__':
    main()
