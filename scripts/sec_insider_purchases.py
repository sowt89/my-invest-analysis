#!/usr/bin/env python3
"""21일(미국 동부 날짜)까지 공개된 Form 4 P 보통주 매수 → 월별 스냅샷.

거래: SEC 분기 TSV + 아직 공개되지 않은 분기의 EDGAR XML.
지수 구성: 기존 저장소와 같은 n100tickers의 기준일 이력 (거래 근거와 구분).
22일 미국장 전 실행. 미래 공시를 소급 포함하지 않는다. 정정/불일치는 검토로 분리.
python scripts/sec_insider_purchases.py [--as-of YYYY-MM-21] [--cache-dir PATH]
"""
import argparse
import calendar
import csv
import io
import json
import re
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SEC = 'https://www.sec.gov'
DATASETS = SEC + '/data-research/sec-markets-data/insider-transactions-data-sets'
MEMBERS_SOURCE = 'https://github.com/jmccarrell/n100tickers'
UA = {'User-Agent': 'my-invest-analysis research sowt89@gmail.com'}


def month_shift(d, months):
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def latest_cutoff(today=None):
    today = today or datetime.now(ZoneInfo('America/New_York')).date()
    # 21일 당일에는 아직 공시가 들어오므로 지난 달의 완료된 스냅샷을 선택.
    d = today.replace(day=21)
    return d if today.day > 21 else month_shift(d, -1)


def as_date(s):
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%d-%b-%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f'Unknown SEC date: {s}')


def num(s):
    try:
        v = float(s)
        return v if abs(v) < float('inf') else None
    except (TypeError, ValueError):
        return None


def common_stock(title):
    t = title.lower()
    return (bool(re.search(r'common|ordinary', t))
            and not re.search(r'preferred|preference|depositary|unit|option|warrant|convertible', t))


class SECClient:
    def __init__(self, cache_dir):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.last = 0
        self.lock = threading.Lock()

    def get(self, url, cache_name=None):
        p = self.cache / cache_name if cache_name else None
        if p and p.exists():
            return p.read_bytes()
        for attempt in range(3):
            # Shared rate limit across four workers, below SEC's 10 requests/s.
            with self.lock:
                time.sleep(max(0, .26 - (time.monotonic() - self.last)))
                self.last = time.monotonic()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                    b = r.read()
                if p:
                    p.write_bytes(b)
                return b
            except urllib.error.HTTPError as e:
                if e.code == 404 or attempt == 2:
                    raise
            except (OSError, TimeoutError):
                if attempt == 2:
                    raise
            time.sleep(2 ** attempt)
        raise RuntimeError('SEC request failed')

    def json(self, url, cache_name=None):
        return json.loads(self.get(url, cache_name))


def tsv(z, name):
    with z.open(name + '.tsv') as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'), delimiter='\t')


def quarter_candidates(blob, symbols, start, cutoff):
    """Bulk data only screens candidates; original XML decides inclusion.

    Include every amendment, even one without P, to detect corrected-away buys.
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        filings = {}
        for r in tsv(z, 'SUBMISSION'):
            filed = as_date(r['FILING_DATE'])
            if (r['ISSUERTRADINGSYMBOL'].upper() in symbols and r['DOCUMENT_TYPE'] in ('4', '4/A')
                    and start <= filed <= cutoff):
                filings[r['ACCESSION_NUMBER']] = {
                    'accession': r['ACCESSION_NUMBER'], 'filed': filed.isoformat(),
                    'cik': str(int(r['ISSUERCIK'])), 'form': r['DOCUMENT_TYPE']}
        wanted = {a for a, r in filings.items() if r['form'] == '4/A'}
        for r in tsv(z, 'NONDERIV_TRANS'):
            a = r['ACCESSION_NUMBER']
            if a in filings and r['TRANS_CODE'] == 'P' and r['TRANS_ACQUIRED_DISP_CD'] == 'A':
                wanted.add(a)
        return [filings[a] for a in sorted(wanted)], set(filings)


def search_filings(client, ciks, start, end):
    """SEC full text index, split by month and CIK batch to stay below 10k cap."""
    results = {}
    for offset in range(0, len(ciks), 25):
        batch = ciks[offset:offset + 25]
        first = start
        while first <= end:
            last = min(end, date(first.year, first.month, calendar.monthrange(first.year, first.month)[1]))
            pos = 0
            while True:
                params = {'dateRange': 'custom', 'startdt': first.isoformat(), 'enddt': last.isoformat(),
                          'forms': '4', 'ciks': ','.join(batch), 'from': pos}
                data = client.json('https://efts.sec.gov/LATEST/search-index?' + urllib.parse.urlencode(params))
                total = data['hits']['total']
                if data.get('timed_out') or data.get('_shards', {}).get('failed', 0):
                    raise RuntimeError('Incomplete SEC search')
                if total['relation'] != 'eq' or total['value'] >= 10000:
                    raise RuntimeError('SEC search result limit exceeded')
                hits = data['hits']['hits']
                if not hits and pos < total['value']:
                    raise RuntimeError('SEC search returned an incomplete page')
                for h in hits:
                    s = h['_source']
                    a, filename = h['_id'].split(':', 1)
                    results[a] = {'accession': a, 'filed': s['file_date'], 'form': s['form'],
                                  'cik': str(int(s['ciks'][0])), 'filename': filename}
                pos += len(hits)
                if pos >= total['value']:
                    break
            first = last + timedelta(days=1)
    return list(results.values())


def read_document(client, filing):
    a = filing['accession']
    base = f"{SEC}/Archives/edgar/data/{filing['cik']}/{a.replace('-', '')}/"
    filename = filing.get('filename')
    if not filename:
        items = client.json(base + 'index.json', a + '-index.json')['directory']['item']
        choices = [r['name'] for r in items if r['name'].endswith('.xml') and '/' not in r['name']]
        if len(choices) != 1:
            raise ValueError(f'Ambiguous primary XML: {a}')
        filename = choices[0]
    url = base + urllib.parse.quote(filename)
    xml = client.get(url, a + '.xml')
    return parse_document(xml, filing, url)


def parse_document(xml, filing, url):
    root = ET.fromstring(xml)
    if root.tag != 'ownershipDocument':
        raise ValueError('Not an ownership document')
    txt = lambda node, path: node.findtext(path, '').strip()
    owners = []
    for r in root.findall('reportingOwner'):
        rel = r.find('reportingOwnerRelationship')
        roles = []
        if rel is not None:
            for field, label in [('isDirector', '이사'), ('isTenPercentOwner', '10% 이상 주주'), ('isOther', '기타')]:
                if txt(rel, field).lower() in ('1', 'true'):
                    roles.append(label)
            if txt(rel, 'isOfficer').lower() in ('1', 'true'):
                roles.append(txt(rel, 'officerTitle') or '임원')
        owners.append({'cik': txt(r, 'reportingOwnerId/rptOwnerCik'),
                       'name': txt(r, 'reportingOwnerId/rptOwnerName'), 'role': ' · '.join(roles)})
    footnotes = {f.attrib['id']: f.text or '' for f in root.findall('footnotes/footnote')}
    doc = {**filing, 'url': url, 'ticker': txt(root, 'issuer/issuerTradingSymbol').upper(),
           'issuer_cik': txt(root, 'issuer/issuerCik'), 'name': txt(root, 'issuer/issuerName'),
           'period': txt(root, 'periodOfReport'), 'original_filed': txt(root, 'dateOfOriginalSubmission'), 'owners': owners,
           'form': txt(root, 'documentType'), 'transactions': []}
    for r in root.findall('nonDerivativeTable/nonDerivativeTransaction'):
        if txt(r, 'transactionCoding/transactionCode') != 'P' or txt(r, 'transactionAmounts/transactionAcquiredDisposedCode/value') != 'A':
            continue
        title = txt(r, 'securityTitle/value')
        if re.search(r'preferred|preference|option|warrant|unit|convertible', title, re.I):
            continue
        shares = num(txt(r, 'transactionAmounts/transactionShares/value'))
        price = num(txt(r, 'transactionAmounts/transactionPricePerShare/value'))
        refs = {f.attrib['id'] for f in r.findall('.//footnoteId')}
        notes = [footnotes[f] for f in sorted(refs) if f in footnotes]
        flags = []
        if not common_stock(title):
            flags.append('보통주 여부 확인 필요')
        if shares is None or shares <= 0 or price is None or price <= 0:
            flags.append('수량·단가 확인 필요')
        if re.search(r'\bsold\b|\bsale\b|\bsales\b', ' '.join(notes), re.I):
            flags.append('P 코드와 매도 관련 각주 대조 필요')
        doc['transactions'].append({
            'date': txt(r, 'transactionDate/value'), 'security': title, 'shares': shares, 'price': price,
            'value': round(shares * price, 2) if shares is not None and price is not None else None,
            'holding': txt(r, 'ownershipNature/directOrIndirectOwnership/value'),
            'ownership': txt(r, 'ownershipNature/natureOfOwnership/value'),
            'post_shares': txt(r, 'postTransactionAmounts/sharesOwnedFollowingTransaction/value'),
            'notes': notes, 'flags': flags})
    return doc


def summarize(documents, start, window_start, cutoff):
    # A 4/A is not reliably a full replacement. Quarantine related originals and
    # corrections together instead of summing or guessing which lines supersede.
    amended = set()
    original_dates = set()
    for d in documents:
        if d['form'] == '4/A' and as_date(d['filed']) <= cutoff:
            for o in d['owners']:
                amended.add((d['issuer_cik'], o['cik'], d['period']))
                if d.get('original_filed'):
                    original_dates.add((d['issuer_cik'], o['cik'], d['original_filed']))
    confirmed, review, seen = [], [], set()
    for d in sorted(documents, key=lambda x: (x['filed'], x['accession'])):
        if as_date(d['filed']) > cutoff:
            continue
        correction = d['form'] == '4/A' or any(
            (d['issuer_cik'], o['cik'], d['period']) in amended
            or (d['issuer_cik'], o['cik'], d['filed']) in original_dates for o in d['owners'])
        for t in d['transactions']:
            if not start <= as_date(t['date']) <= cutoff:
                continue
            flags = list(t['flags'])
            if correction:
                flags.append('정정공시 대조 필요 · 합계 제외')
            row = {**t, 'ticker': d['ticker'], 'name': d['name'], 'owners': d['owners'],
                   'filed': d['filed'], 'accession': d['accession'], 'url': d['url'], 'flags': flags}
            # Joint reports can repeat the same economic trade. Different price
            # fills in the same document remain separate, then count as one day.
            keys = [(d['issuer_cik'], o['cik'], t['date'], t['security'], t['shares'], t['price'],
                     t['holding'], t['ownership'], t['post_shares']) for o in d['owners']]
            if any(k in seen for k in keys):
                continue
            seen.update(keys)
            (review if flags else confirmed).append(row)
    groups = defaultdict(list)
    for t in confirmed:
        groups[t['ticker']].append(t)
    companies = []
    for ticker, rows in groups.items():
        current = [r for r in rows if as_date(r['date']) >= window_start]
        dates = sorted({r['date'] for r in rows})
        months = sorted({d[:7] for d in dates})
        owners = {o['cik']: o for r in current for o in r['owners']}
        owner_days = defaultdict(set)
        for r in rows:
            for o in r['owners']:
                owner_days[o['cik']].add(r['date'])
        companies.append({'ticker': ticker, 'name': rows[0]['name'], 'active': bool(current),
                          'value': round(sum(r['value'] for r in current), 2),
                          'six_month_value': round(sum(r['value'] for r in rows), 2),
                          'dates': dates, 'month_count': len(months), 'day_count': len(dates),
                          'current_day_count': len({r['date'] for r in current}),
                          'buyer_count': len(owners), 'buyers': list(owners.values()),
                          'same_buyer_repeat': any(len(owner_days[o]) >= 2 for o in owners),
                          'pattern': ('최근 매수 없음' if not current else '여러 달 반복' if len(months) >= 2
                                      else '단기 반복' if len(dates) >= 2 else '단일 매수일'),
                          'transactions': sorted(rows, key=lambda r: r['date'], reverse=True)})
    companies.sort(key=lambda c: (-c['active'], -c['value'], c['ticker']))
    return companies, review


def collect(cutoff, client):
    from nasdaq_100_ticker_history import tickers_as_of
    symbols = {t.upper() for t in tickers_as_of(cutoff.year, cutoff.month, cutoff.day)}
    if not 95 <= len(symbols) <= 110:
        raise ValueError(f'Unexpected Nasdaq100 constituent count: {len(symbols)}')
    start = month_shift(cutoff, -6) + timedelta(days=1)
    window_start = month_shift(cutoff, -1) + timedelta(days=1)
    mapping = client.json(SEC + '/files/company_tickers.json')
    by_ticker = {r['ticker'].upper(): str(r['cik_str']).zfill(10) for r in mapping.values()}
    missing = sorted(symbols - by_ticker.keys())
    ciks = sorted({by_ticker[t] for t in symbols if t in by_ticker})
    filings, ranges, sources, covered, errors = {}, [], [], set(), []
    qstart = date(start.year, ((start.month - 1) // 3) * 3 + 1, 1)
    while qstart <= cutoff:
        qend = month_shift(qstart, 3) - timedelta(days=1)
        name = f'{qstart.year}q{(qstart.month-1)//3+1}_form345.zip'
        url = f'{SEC}/files/datastandardsinnovation/data/insider-transactions-data-sets/{name}'
        try:
            blob = client.get(url, name)
            candidates, known = quarter_candidates(blob, symbols, start, cutoff)
            filings.update({f['accession']: f for f in candidates})
            covered.update(known)
            sources.append(url)
            # SEC moves filings after 17:30 on the last business day into next
            # quarter. Explicitly cover this boundary rather than assume completeness.
            if cutoff >= qend - timedelta(days=6):
                ranges.append((max(start, qend - timedelta(days=6)), min(cutoff, qend)))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as e:
            print(f'Quarter unavailable ({name}); using EDGAR: {type(e).__name__}', flush=True)
            ranges.append((max(start, qstart), min(cutoff, qend)))
        qstart = month_shift(qstart, 3)
    for first, last in ranges:
        print(f'EDGAR search {first} → {last}', flush=True)
        try:
            for f in search_filings(client, ciks, first, last):
                if f['accession'] not in covered or f['accession'] in filings:
                    filings[f['accession']] = f
        except Exception as e:
            errors.append(f'{first}~{last}: {type(e).__name__}: {e}')
    docs = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(read_document, client, f): f for f in filings.values()}
        for i, future in enumerate(as_completed(pending), 1):
            f = pending[future]
            try:
                d = future.result()
                if d['ticker'] in symbols and start <= as_date(d['filed']) <= cutoff:
                    docs.append(d)
            except Exception as e:
                errors.append(f"{f['accession']}: {type(e).__name__}: {e}")
            if i % 20 == 0 or i == len(pending):
                print(f'XML {i}/{len(pending)} · failures {len(errors)}', flush=True)
    companies, review = summarize(docs, start, window_start, cutoff)
    return {'schema_version': 1, 'as_of': cutoff.isoformat(), 'timezone': 'America/New_York',
            'window_start': window_start.isoformat(), 'lookback_start': start.isoformat(),
            'generated_at': datetime.now(ZoneInfo('UTC')).isoformat(),
            'universe': {'label': '나스닥100 전체', 'source': MEMBERS_SOURCE,
                         'as_of': cutoff.isoformat(), 'tickers': sorted(symbols), 'missing_cik': missing},
            'coverage': {'complete': not errors and not missing, 'errors': errors,
                         'bulk_filing_count': len(covered), 'xml_count': len(docs), 'sources': sources,
                         'note': '기준일까지 공개된 공시만 포함. 미신고·지연공시·Form 4 의무가 없는 발행인의 거래는 확인할 수 없습니다.'},
            'companies': companies, 'review': review}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--as-of', type=date.fromisoformat)
    p.add_argument('--cache-dir', default=str(ROOT / '.cache' / 'sec-insiders'))
    p.add_argument('--output-dir', default=str(ROOT / 'data'))
    args = p.parse_args()
    cutoff = args.as_of or latest_cutoff()
    if cutoff.day != 21 or cutoff > latest_cutoff():
        p.error('기준일은 완료된 미국 동부 날짜의 21일이어야 합니다.')
    report = collect(cutoff, SECClient(args.cache_dir))
    out = Path(args.output_dir)
    (out / 'insider-history').mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    snapshot = out / 'insider-history' / f'{cutoff}.json'
    # Never overwrite a complete historical snapshot with a partial retry.
    if snapshot.exists() and json.loads(snapshot.read_text())['coverage']['complete']:
        report = json.loads(snapshot.read_text())
        text = snapshot.read_text()
    else:
        snapshot.write_text(text, encoding='utf-8')
    latest = out / 'insider-purchases.json'
    if not latest.exists() or json.loads(latest.read_text())['as_of'] <= report['as_of']:
        latest.write_text(text, encoding='utf-8')
    print(json.dumps({'as_of': report['as_of'], 'complete': report['coverage']['complete'],
                      'active': sum(c['active'] for c in report['companies']),
                      'review': len(report['review']), 'errors': report['coverage']['errors']}, ensure_ascii=False))
    if not report['coverage']['complete']:
        raise SystemExit('Partial report written; see coverage errors before use.')


if __name__ == '__main__':
    main()
