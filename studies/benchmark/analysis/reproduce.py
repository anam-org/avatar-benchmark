#!/usr/bin/env python3
"""Recompute the study's figures from an evidence bundle.

Standard library only, no dependencies.

    python3 analysis/reproduce.py                      # uses evidence/x1ecy3l
    python3 analysis/reproduce.py --bundle <dir>
    python3 analysis/reproduce.py --check-envelopes    # re-derive turn latency
                                                       # from the raw audio

Bootstrap figures use a fixed seed, so repeated runs agree exactly.
"""
import argparse
import csv
import gzip
import json
import math
import random
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

# The arms the bundle fields, sorted. Set by load() from the ratings.
PROV: list[str] = []
# Width of the arm-name column, widened to the longest arm by main()/load().
PW: int = 12
# The arm every comparison is expressed against. Named "anam" when the base
# arm is fielded; a study that fields only a pinned Anam arm uses that one.
REF: str = "anam"


def vendor(arm: str) -> str:
    """The provider an arm belongs to. Arms are named `<vendor>-<model>`, and a
    vendor may field several: a pinned Anam arm and the base arm are both
    anam."""
    return arm.split('-')[0]


def siblings(arm: str) -> set[str]:
    """Every fielded arm from the same vendor, the arm itself included."""
    return {p for p in PROV if vendor(p) == vendor(arm)}
AX = [('visual_quality', 'Visual quality'), ('naturalness', 'Naturalness'),
      ('lip_sync_quality', 'Lip sync'), ('interruptibility', 'Interruptibility'),
      ('response_time', 'Response time'), ('overall_experience', 'Overall experience')]
AXK = [k for k, _ in AX]
SEED = 20260729
BOOT = 20000
SD_FLOOR = 0.3          # an SD below this is replaced by 1.0
MIN_TESTABLE = 10       # smallest group we publish a p-value for
MERGE_GAP = 0.80        # speech bursts closer than this are one turn
THRESH = -55.0
MIN_DUR = 0.30


# ---------------------------------------------------------------- helpers
def p_from_t(t, df=None):
    """Two-sided p-value. Student-t when df is given, normal otherwise."""
    if df is None or df <= 0:
        return 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    x = df / (df + t * t)

    def betacf(a, b, x):
        c, d = 1.0, 1 - (a + b) * x / (a + 1)
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        h = d
        for m in range(1, 300):
            m2 = 2 * m
            aa = m * (b - m) * x / ((a - 1 + m2) * (a + m2))
            d = 1 + aa * d
            c = 1 + aa / c
            d = 1 / (d if abs(d) > 1e-300 else 1e-300)
            h *= d * c
            aa = -(a + m) * (a + b + m) * x / ((a + m2) * (a + 1 + m2))
            d = 1 + aa * d
            c = 1 + aa / c
            d = 1 / (d if abs(d) > 1e-300 else 1e-300)
            de = d * c
            h *= de
            if abs(de - 1) < 3e-14:
                break
        return h

    def betai(a, b, x):
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        lb = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                      + a * math.log(x) + b * math.log(1 - x))
        return (lb * betacf(a, b, x) / a if x < (a + 1) / (a + b + 2)
                else 1 - lb * betacf(b, a, 1 - x) / b)
    return betai(df / 2, 0.5, x)


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float('nan')
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def sem(v):
    """Standard error of the mean; nan for a single observation."""
    return st.stdev(v) / math.sqrt(len(v)) if len(v) > 1 else float('nan')


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return float('nan')
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return num / (dx * dy) if dx and dy else float('nan')


def paired(a, b):
    """mean difference, standard error, sigma, p — over raters present in both."""
    ids = set(a) & set(b)
    d = [a[i] - b[i] for i in ids]
    m = st.mean(d)
    se = st.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else float('nan')
    return (m, se, (m / se if se else float('nan')),
            p_from_t(m / se if se else 0, len(d) - 1), len(d))


def head(title):
    print('\n' + '=' * 78)
    print(title)
    print('=' * 78)


def sub(title):
    print('\n--- ' + title)


def arm_of(row):
    """The arm a session or turn row belongs to; the vendor where no arm
    column is present."""
    return row.get('arm') or row['provider']


# ---------------------------------------------------------------- load
def load(bundle):
    d = {}
    d['ratings'] = list(csv.DictReader(open(bundle / 'ratings.csv')))
    d['raters'] = {r['rater_id']: r for r in csv.DictReader(open(bundle / 'rater_sessions.csv'))}
    d['sessions'] = {e['session_uuid']: e for e in csv.DictReader(open(bundle / 'session_evidence.csv'))}
    d['join'] = {j['slate_id']: j for j in csv.DictReader(open(bundle / 'ratings_evidence_join.csv'))}
    d['turns'] = {t['session_uuid']: t for t in csv.DictReader(open(bundle / 'session_turn_metrics.csv'))}
    lat = defaultdict(list)
    for r in csv.DictReader(open(bundle / 'turn_latencies.csv')):
        lat[r['session_uuid']].append(float(r['latency_s']))
    d['lat'] = lat
    global PROV, REF, PW
    PROV = sorted({r['provider'] for r in d['ratings']})
    PW = max((len(p) for p in PROV), default=10) + 2
    REF = ('anam' if 'anam' in PROV
           else next((p for p in PROV if p.startswith('anam')), PROV[0]))
    d['demographics'] = json.load(open(bundle / 'demographics_aggregate.json'))
    cfg = json.load(open(bundle / 'study_config.json'))
    d['practice_topic'] = cfg.get('practice_topic')
    return d


def normalise(ratings):
    """Per-rater z-scores. Returns slate_id -> {axis: z} and rater -> provider -> composite."""
    by_rater = defaultdict(list)
    for r in ratings:
        by_rater[r['rater_id']].append(r)
    z, per = {}, defaultdict(dict)
    for rid, rows in by_rater.items():
        acc = defaultdict(list)
        for a in AXK:
            v = [float(x[a]) for x in rows if x[a] != '']
            m, s = st.mean(v), st.pstdev(v)
            s = s if s >= SD_FLOOR else 1.0
            for x in rows:
                if x[a] != '':
                    zz = (float(x[a]) - m) / s
                    z.setdefault(x['slate_id'], {})[a] = zz
                    acc[x['provider']].append(zz)
        for p, vals in acc.items():
            per[rid][p] = st.mean(vals)
    return z, per


def axis_per_rater(ratings, axis):
    by_rater = defaultdict(list)
    for r in ratings:
        by_rater[r['rater_id']].append(r)
    out = defaultdict(dict)
    for rid, rows in by_rater.items():
        v = [float(x[axis]) for x in rows if x[axis] != '']
        m, s = st.mean(v), st.pstdev(v)
        s = s if s >= SD_FLOOR else 1.0
        acc = defaultdict(list)
        for x in rows:
            if x[axis] != '':
                acc[x['provider']].append((float(x[axis]) - m) / s)
        for p, vals in acc.items():
            out[rid][p] = st.mean(vals)
    return out


def check_shape(d, bundle):
    """Refuse a bundle the figures cannot be computed from, and say why."""
    by_rater = defaultdict(set)
    for r in d['ratings']:
        by_rater[r['rater_id']].add(r['provider'])
    short = sorted(rid for rid, ps in by_rater.items() if set(PROV) - ps)
    if not short:
        return
    missing = sorted({p for ps in by_rater.values() for p in set(PROV) - ps})
    raise SystemExit(
        f"FATAL: {bundle} cannot be analysed.\n"
        f"  {len(short)} of {len(by_rater)} raters did not rate all"
        f" {len(PROV)} arms (e.g. {short[0]}).\n"
        f"  Arms left uncovered by at least one rater: {', '.join(missing)}.\n"
        f"  A job needs at least as many rated conversations per rater as\n"
        f"  there are arms.")


# ---------------------------------------------------------------- sections
def s_dataset(d):
    head('DATASET')
    r = d['ratings']
    print(f'  rated conversations       {len(r):,}')
    print(f'  raters                    {len({x["rater_id"] for x in r})}')
    print(f'  per provider              {dict(Counter(x["provider"] for x in r))}')
    counts = Counter(tuple(sorted(Counter(x['provider'] for x in r if x['rater_id'] == rid).values()))
                     for rid in {x['rater_id'] for x in r})
    each = len([x for x in r if x['rater_id'] == next(iter({y['rater_id'] for y in r}))]) // len(PROV)
    print(f'  per-rater balance         {dict(counts)}   (expect one pattern: {each} of each)')
    print(f'  avatar sessions recorded  {len(d["sessions"]):,}')


def s_headline(d, per):
    head('THE HEADLINE')
    raters = list(per)
    rnd = random.Random(SEED)
    means = {p: st.mean([per[r][p] for r in raters if p in per[r]]) for p in PROV}
    boots = {p: [] for p in PROV}
    first = Counter()
    second = Counter()
    for _ in range(BOOT):
        s = [rnd.choice(raters) for _ in raters]
        m = {p: st.mean([per[r][p] for r in s if p in per[r]]) for p in PROV}
        for p in PROV:
            boots[p].append(m[p])
        order = sorted(PROV, key=lambda p: -m[p])
        first[order[0]] += 1
        second[order[1]] += 1
    print(f'  {"provider":<{PW}}{"composite":>11}{"95% CI":>22}{"vs Anam":>22}')
    for p in sorted(PROV, key=lambda p: -means[p]):
        b = sorted(boots[p])
        ci = f'{b[int(BOOT*.025)]:+.3f} to {b[int(BOOT*.975)]:+.3f}'
        if p == REF:
            gap = ''
        else:
            m, se, sig, pv, n = paired({r: per[r][REF] for r in raters if REF in per[r]},
                                       {r: per[r][p] for r in raters if p in per[r]})
            gap = f'{m:.3f} ({sig:.1f} sigma)'
        print(f'  {p:<{PW}}{means[p]:>+11.3f}{ci:>22}{gap:>22}')
    print(f'\n  P({REF} ranks first)       {first[REF]/BOOT:.4f}   '
          f'({first[REF]:,} of {BOOT:,} resamples)')
    sub('second place')
    for p in sorted(PROV, key=lambda p: -second[p]):
        if p != REF:
            print(f'  P({p} second)  {second[p]/BOOT:.0%}')
    rivals = [p for p in PROV if p not in siblings(REF)]
    spread = max(means[p] for p in rivals) - min(means[p] for p in rivals)
    print(f'  competitor spread         {spread:.3f}')
    others = rivals
    for i, a in enumerate(others):
        for b in others[i+1:]:
            xs = {r: per[r][a] for r in raters if a in per[r]}
            ys = {r: per[r][b] for r in raters if b in per[r]}
            if not set(xs) & set(ys):
                continue
            m, se, sig, pv, n = paired(xs, ys)
            print(f'  {a} vs {b}: {m:+.3f} ({sig:+.2f} sigma, p={pv:.3f})')


def s_distribution(d):
    head('SHAPE OF THE RATINGS')
    print(f'  {"provider":<{PW}}' + ''.join(f'{lab:>8}' for lab in ['-2', '-1', '0', '+1', '+2'])
          + f'{"negative":>11}')
    counts = set()
    for p in PROV:
        v = [float(x[a]) for x in d['ratings'] if x['provider'] == p for a in AXK if x[a] != '']
        c = Counter(v)
        n = len(v)
        counts.add(n)
        print(f'  {p:<{PW}}' + ''.join(f'{c[s]/n*100:>7.0f}%' for s in [-2.0, -1.0, 0.0, 1.0, 2.0])
              + f'{sum(1 for x in v if x < 0)/n*100:>10.1f}%')
    if len(counts) == 1:
        n = counts.pop()
        print(f'  (each provider rated {n:,} times: {len(AXK)} statements x '
              f'{n // len(AXK):,} conversations)')
    else:
        print(f'  (ratings per provider are NOT equal: {sorted(counts)})')


def s_axes(d):
    head('PER STATEMENT')
    cw = max(9, max(len(p) for p in PROV) + 1)
    nw = max(28, max(len(p) for p in PROV) + 22)
    print(f'  {"statement":<20}' + ''.join(f'{p:>{cw}}' for p in PROV)
          + f'{REF + " lead":>{nw}}')
    for a, lab in AX:
        pr = axis_per_rater(d['ratings'], a)
        means = {p: st.mean([pr[r][p] for r in pr if p in pr[r]]) for p in PROV}
        best = max((means[p], p) for p in PROV if p not in siblings(REF))[1]
        m, se, sig, pv, n = paired({r: pr[r][REF] for r in pr}, {r: pr[r][best] for r in pr})
        note = f'{m:+.3f} vs {best} (p={pv:.3f})'
        print(f'  {lab:<20}' + ''.join(f'{means[p]:>+{cw}.2f}' for p in PROV)
              + f'{note:>{nw}}')
    sub('among the competitors, per statement')
    for a, lab in AX:
        pr = axis_per_rater(d['ratings'], a)
        hits = []
        others = [q for q in PROV if q not in siblings(REF)]
        for i, x in enumerate(others):
            for y in others[i+1:]:
                xs = {r: pr[r][x] for r in pr if x in pr[r]}
                ys = {r: pr[r][y] for r in pr if y in pr[r]}
                if not set(xs) & set(ys):
                    continue
                m, se, sig, pv, n = paired(xs, ys)
                if pv < 0.05:
                    hits.append(f'{x} vs {y} {m:+.3f} (p={pv:.3f})')
        print(f'  {lab:<20}' + ('; '.join(hits) if hits else 'no significant difference'))


def s_scoring(d, per):
    head('SCORING SENSITIVITY')
    raw = defaultdict(dict)
    by_rater = defaultdict(list)
    for r in d['ratings']:
        by_rater[r['rater_id']].append(r)
    for rid, rows in by_rater.items():
        acc = defaultdict(list)
        for x in rows:
            acc[x['provider']].append(st.mean([float(x[a]) for a in AXK if x[a] != '']))
        for p, v in acc.items():
            raw[rid][p] = st.mean(v)
    print(f'  {"provider":<{PW}}{"raw mean":>11}{"normalised":>13}')
    for p in sorted(PROV, key=lambda p: -st.mean([per[r][p] for r in per])):
        print(f'  {p:<{PW}}{st.mean([raw[r][p] for r in raw]):>+11.3f}'
              f'{st.mean([per[r][p] for r in per]):>+13.3f}')
    for label, store in (('raw', raw), ('normalised', per)):
        means = {p: st.mean([store[r][p] for r in store]) for p in PROV}
        best = max((means[p], p) for p in PROV if p not in siblings(REF))[1]
        m, se, sig, pv, n = paired({r: store[r][REF] for r in store},
                                   {r: store[r][best] for r in store})
        print(f'  Anam lead over {best}, {label:<11} {sig:.2f} sigma')
    print('')


def s_avatars(d):
    head('PER AVATAR')
    z, _ = normalise(d['ratings'])
    cells = defaultdict(list)
    for x in d['ratings']:
        zz = z.get(x['slate_id'])
        if zz:
            cells[(x['provider'], x['slot'])].append(st.mean([zz[a] for a in AXK if a in zz]))
    rows = sorted(((st.mean(v), sem(v), len(v), k) for k, v in cells.items()),
                  reverse=True)
    for i, (m, se, n, (p, s)) in enumerate(rows, 1):
        mark = ' <-- weakest Anam' if p == REF and i == max(
            j for j, (_, _, _, (pp, _)) in enumerate(rows, 1) if pp == REF) else ''
        print(f'  {i:>2}. {p:<{PW}}{s}  {m:>+7.3f} +/- {se:.3f}  (n={n}){mark}')


def s_connect(d):
    head('CONNECTING')
    print(f'  {"provider":<{PW}}' + ''.join(f'{f"p{q}":>9}' for q in [50, 75, 90, 95, 99]))
    for p in PROV:
        v = [int(e['connect_ms']) / 1000 for e in d['sessions'].values()
             if arm_of(e) == p and e['connect_ms']
             and e['topic'] != d['practice_topic']]
        print(f'  {p:<{PW}}' + ''.join(f'{pct(v, q):>8.1f}s' for q in [50, 75, 90, 95, 99]))

    # connect_ms contains the provider's mint call; split it out.
    sub('of which: the provider mint call, and what is left')
    print(f'  {"provider":<{PW}}{"mint p50":>10}{"mint p90":>10}'
          f'{"rest p50":>10}{"rest p90":>10}{"n":>7}')
    for p in PROV:
        pairs = [(int(e['connect_ms']) / 1000, int(e['mint_ms']) / 1000)
                 for e in d['sessions'].values()
                 if arm_of(e) == p and e.get('connect_ms') and e.get('mint_ms')
                 and e['topic'] != d['practice_topic']]
        if not pairs:
            print(f'  {p:<{PW}}{"-":>10}{"-":>10}{"-":>10}{"-":>10}{0:>7}')
            continue
        mint = [m for _, m in pairs]
        rest = [c - m for c, m in pairs]
        print(f'  {p:<{PW}}{pct(mint, 50):>9.2f}s{pct(mint, 90):>9.2f}s'
              f'{pct(rest, 50):>9.2f}s{pct(rest, 90):>9.2f}s{len(pairs):>7}')
    sub('per-rater: which provider connected fastest')
    per = defaultdict(dict)
    for x in d['ratings']:
        j = d['join'].get(x['slate_id'])
        if not j or not j['session_uuid']:
            continue
        e = d['sessions'].get(j['session_uuid'])
        if e and e['connect_ms']:
            per[x['rater_id']].setdefault(x['provider'], []).append(int(e['connect_ms']))
    full = {r: {p: st.mean(v) for p, v in d_.items()}
            for r, d_ in per.items() if len(d_) == len(PROV)}
    wins = Counter(min(v, key=v.get) for v in full.values())
    print(f'  raters with all {len(PROV)} measured: {len(full)}')
    for p in PROV:
        print(f'    {p:<{PW}} fastest for {wins[p]:>3} of them')


def s_latency(d):
    head('RESPONDING')
    good = {u: v for u, v in d['lat'].items() if len(v) >= 3}
    prov_of = {t['session_uuid']: arm_of(t) for t in d['turns'].values()}
    by = defaultdict(list)
    for u, v in good.items():
        by[prov_of.get(u)].extend(v)
    print(f'  {"provider":<{PW}}{"turns":>8}' + ''.join(f'{f"p{q}":>8}' for q in [50, 75, 90, 95])
          + f'{"over 3s":>10}')
    for p in sorted(PROV, key=lambda p: pct(by[p], 50)):
        v = by[p]
        print(f'  {p:<{PW}}{len(v):>8}' + ''.join(f'{pct(v, q):>7.2f}s' for q in [50, 75, 90, 95])
              + f'{sum(1 for x in v if x > 3)/len(v)*100:>9.1f}%')
    sub('session-median unit, within rater')
    med = {u: st.median(v) for u, v in good.items()}
    per = defaultdict(dict)
    for x in d['ratings']:
        j = d['join'].get(x['slate_id'])
        if j and j['session_uuid'] in med:
            per[x['rater_id']].setdefault(x['provider'], []).append(med[j['session_uuid']])
    pm = {r: {p: st.mean(v) for p, v in dd.items()} for r, dd in per.items()}
    for p in [q for q in PROV if q != REF]:
        a = {r: v[REF] for r, v in pm.items() if REF in v and p in v}
        b = {r: v[p] for r, v in pm.items() if REF in v and p in v}
        m, se, sig, pv, n = paired(a, b)
        print(f'  Anam minus {p:<{PW}} {m:+.3f}s ({sig:+.1f} sigma, n={n} raters)')


def s_perception(d):
    head('RATINGS AGAINST TELEMETRY')
    z, _ = normalise(d['ratings'])
    rec = []
    for x in d['ratings']:
        j = d['join'].get(x['slate_id'])
        if not j or not j['session_uuid']:
            continue
        e = d['sessions'].get(j['session_uuid'])
        zz = z.get(x['slate_id'])
        if not e or not e['connect_ms'] or not zz:
            continue
        L = d['lat'].get(j['session_uuid'], [])
        rec.append(dict(rater=x['rater_id'],
                        prov=x['provider'], connect=int(e['connect_ms']) / 1000,
                        med=st.median(L) if len(L) >= 3 else None,
                        slow=(sum(1 for v in L if v > 3) / len(L)) if len(L) >= 3 else None,
                        rt=zz.get('response_time')))
    by_rater = defaultdict(list)
    for r_ in rec:
        by_rater[r_['rater']].append(r_)
    raters = list(by_rater)

    def pooled(rows, key):
        xs, ys = [], []
        for p in PROV:                       # centre within provider
            g = [r for r in rows if r['prov'] == p
                 and r[key] is not None and r['rt'] is not None]
            if len(g) < 3:
                continue
            mf, my = st.mean([r[key] for r in g]), st.mean([r['rt'] for r in g])
            xs += [r[key] - mf for r in g]
            ys += [r['rt'] - my for r in g]
        return (pearson(xs, ys) if len(xs) >= 3 else None), len(xs)

    for key, lab in (('slow', 'share of turns over 3 s'), ('connect', 'connection time'),
                     ('med', 'median turn latency')):
        r, n = pooled(rec, key)
        rnd = random.Random(SEED)
        bs = []
        for _ in range(BOOT):
            samp = [row for _ in raters for row in by_rater[rnd.choice(raters)]]
            rb, _ = pooled(samp, key)
            if rb is not None:
                bs.append(rb)
        bs.sort()
        lo, hi = bs[int(len(bs) * .025)], bs[int(len(bs) * .975)]
        above = sum(1 for v in bs if v >= 0) / len(bs)
        pv = 2 * min(above, 1 - above)
        print(f'  {lab:<26} r={r:+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]  '
              f'p={pv:.3f}  n={n}')
        per_p = []
        for p in PROV:
            rows = [r_ for r_ in rec if r_['prov'] == p and r_[key] is not None and r_['rt'] is not None]
            per_p.append(f'{p[:4]} {pearson([r_[key] for r_ in rows], [r_["rt"] for r_ in rows]):+.3f}')
        print('    per provider: ' + '  '.join(per_p))
    sub('halo: how correlated are the six statements within one conversation?')
    full = [z[s] for s in z if len(z[s]) == 6]
    offs = []
    for i, (a, la) in enumerate(AX):
        for b, lb in AX[i+1:]:
            r = pearson([v[a] for v in full], [v[b] for v in full])
            offs.append(r)
            if (a, b) == ('visual_quality', 'response_time'):
                print(f'  visual quality vs response time  r={r:+.3f}')
    print(f'  mean correlation between any two statements  r={st.mean(offs):+.3f}')


def live(e):
    """Did this session ever start?"""
    return e['end_state'] != 'error-before-connect'


def study(d):
    return [e for e in d['sessions'].values()
            if e['topic'] != d['practice_topic']]


def dpr(v):
    try:
        return round(float(v), 6)
    except (TypeError, ValueError):
        return None


def session_device(e):
    """The machine a session ran on, read from its own client manifest."""
    ua = e['user_agent']
    plat = ('win' if 'Windows' in ua else 'mac' if 'Macintosh' in ua
            else 'linux' if 'Linux' in ua else 'other')
    if 'Edg/' in ua:
        br, ver = 'edge', ua.split('Edg/')[1].split(' ')[0]
    elif 'Firefox/' in ua:
        br, ver = 'firefox', ua.split('Firefox/')[1].split(' ')[0]
    elif 'Chrome/' in ua:
        br, ver = 'chrome', ua.split('Chrome/')[1].split(' ')[0]
    else:
        br, ver = '?', '?'
    return (plat, br, ver, dpr(e['device_pixel_ratio']))


def rater_device(m):
    """The same tuple, read from the rater's self-reported environment."""
    os_name = m['os_name']
    plat = ('win' if 'Windows' in os_name else 'mac' if 'macOS' in os_name
            else 'linux' if 'Linux' in os_name else 'other')
    b = m['browser'].lower()
    br = ('edge' if 'edge' in b else 'firefox' if 'firefox' in b
          else 'chrome' if 'chrome' in b else '?')
    return (plat, br, m['browser_version'], dpr(m['device_pixel_ratio']))


def never_reached_ids(d, provider=None):
    """Raters who never once reached a working session with `provider`.

    Only raters whose device is unique among everyone active in their window
    can be named at all; the rest are unattributable and are skipped.
    """
    if any('browser_version' not in m or 'date_created' not in m
           for m in d['raters'].values()):
        return set()
    provider = provider or REF
    sess = study(d)
    out = []
    for rid, m in sorted(d['raters'].items()):
        t0, t1 = m['date_created'], m['date_completed']
        dev = rater_device(m)
        if any(q != rid and rater_device(mm) == dev
               and not (mm['date_completed'] < t0 or mm['date_created'] > t1)
               for q, mm in d['raters'].items()):
            continue
        mine = [e for e in sess
                if t0 <= e['recorded_at_utc'] <= t1 and session_device(e) == dev
                and arm_of(e) == provider]
        if mine and not any(live(e) for e in mine):
            out.append(rid)
    return set(out)


def s_never_reached(d, per):
    head('RATERS WHO NEVER REACHED A PROVIDER')
    if any('browser_version' not in m or 'date_created' not in m
           for m in d['raters'].values()):
        print('  Skipped: individual device fingerprints and timestamps are not published.')
        return
    sess = study(d)
    print('  A rater can only be named where their device is unique among everyone')
    print('  active at the same time.')
    skipped = sum(1 for rid, m in d['raters'].items()
                  if any(q != rid and rater_device(mm) == rater_device(m)
                         and not (mm['date_completed'] < m['date_created']
                                  or mm['date_created'] > m['date_completed'])
                         for q, mm in d['raters'].items()))
    per_provider = {p: sorted(never_reached_ids(d, provider=p)) for p in PROV}
    for p in PROV:
        ids_p = per_provider[p]
        print(f'  never reached {p:<{PW}} {", ".join(ids_p) if ids_p else "none"}')
    named = []
    for rid in sorted(never_reached_ids(d)):
        m = d['raters'][rid]
        t0, t1 = m['date_created'], m['date_completed']
        mine = [e for e in sess if t0 <= e['recorded_at_utc'] <= t1
                and session_device(e) == rater_device(m)]
        named.append((rid, m, mine, [e for e in mine if arm_of(e) == REF]))
    print(f'  raters whose device is NOT unique in their window (unattributable): {skipped}')
    conf = Counter(j.get('confidence', '?') for j in d['join'].values())
    print('  join confidence, which the bundle publishes per rating: '
          + ', '.join(f'{k}={conf.get(k, 0)}' for k in
                      ('exact', 'unique', 'probable', 'ambiguous', 'none')))
    for rid, m, mine, a in named:
        print(f"\n  {rid}  {m['country_of_residence']}, {m['browser']} {m['browser_version']}, "
              f"dpr {m['device_pixel_ratio']}")
        print(f"    Anam attempts {len(a):>3}, of which started: {sum(1 for e in a if live(e))}")
        for p in PROV:
            v = [e for e in mine if arm_of(e) == p]
            print(f"      {p:<{PW}} {len(v):>3} sessions, {sum(1 for e in v if live(e))} started")
        sc = [float(x[k]) for x in d['ratings'] if x['rater_id'] == rid
              and x['provider'] == REF for k in AXK if x[k] != '']
        print(f"    their Anam responses: n={len(sc)}, all equal to "
              f"{sc[0]:+.0f}: {len(set(sc)) == 1}")
        print('    raw mean by provider: ' + '  '.join(
            f'{p} {st.mean([float(x[k]) for x in d["ratings"] if x["rater_id"] == rid and x["provider"] == p for k in AXK if x[k] != ""]):+.2f}'
            for p in PROV))
    ids = [n[0] for n in named]
    print(f'\n  detail below is for {REF}: '
          + (', '.join(ids) if ids else f'no rater failed to reach {REF}'))
    sub('effect of removing them' if ids else 'subgroup leads')

    def lead(rs):
        mm = {p: st.mean([per[r][p] for r in rs if p in per[r]]) for p in PROV}
        b = max((mm[p], p) for p in PROV if p != REF)
        return mm[REF] - b[0], b[1]

    def margin(r):
        return per[r][REF] - st.mean([per[r][q] for q in PROV if q != REF])
    allr = set(per)

    def row(lab, rs):
        if len(rs) < 2:
            print(f'  {lab:<32} n={len(rs):>3}  too few to test')
            return
        g, b = lead(rs)
        v = [margin(r) for r in rs]
        se_ = sem(v)
        tt = st.mean(v) / se_ if se_ == se_ and se_ else float('nan')
        print(f'  {lab:<32} n={len(rs):>3}  vs 2nd {g:+.3f} ({b})  '
              f'vs mean-of-others {st.mean(v):+.3f}  p={p_from_t(tt, len(v) - 1):.4f}')

    row('all raters', allr)
    if ids:
        row(f'all, minus those {len(ids)}', allr - set(ids))
    for c in ['United States', 'United Kingdom', 'Canada']:
        rs = {r for r in allr if d['raters'][r]['country_of_residence'] == c}
        row(c, rs)
        if ids and c == 'United States':
            row(f'United States, minus those {len(ids)}', rs - set(ids))
    sub('the lowest per-rater margins')
    ranked = sorted(allr, key=margin)
    for r in ranked[:4]:
        print(f'    {r:<10}{margin(r):>+8.3f}  {d["raters"][r]["country_of_residence"]}'
              f'{"   <-- never reached anam" if r in ids else ""}')


def s_pool(d, per):
    head('WHO RATED')
    m = d['raters']
    ages = [int(age) for age, count in d['demographics']['age'].items()
            if str(age).isdigit() for _ in range(count)]
    print(f'  raters {len(m)} | age {min(ages)}-{max(ages)}, median {int(st.median(ages))}')
    for f in ['sex', 'country_of_residence', 'ethnicity', 'employment', 'os_name']:
        c = (Counter(d['demographics'][f]) if f == 'ethnicity' else
             Counter(v[f] or 'not stated' for v in m.values()))
        if f == 'ethnicity' and sum(c.values()) < len(m):
            c['not stated'] += len(m) - sum(c.values())
        print(f'  {f:<22}' + ', '.join(f'{k} {n}' for k, n in c.most_common()))

    def margin_mean(rs):
        return st.mean([per[r][REF] - st.mean([per[r][q] for q in PROV if q != REF])
                        for r in rs])

    def margin_second(rs):
        means = {q: st.mean([per[r][q] for r in rs]) for q in PROV if q != REF}
        b = max(means, key=means.get)
        return st.mean([per[r][REF] - per[r][b] for r in rs])

    def pv(rs):
        dd = [per[r][REF] - st.mean([per[r][q] for q in PROV if q != REF]) for r in rs]
        if len(dd) < 2:
            return None
        se = sem(dd)
        return p_from_t(st.mean(dd) / se, len(dd) - 1) if se else None

    def band(a):
        # No age given.
        if a in (None, ''):
            return 'not stated'
        if a in ('18-29', '30-39', '40-49', '50+'):
            return a
        a = int(a)
        return '18-29' if a < 30 else '30-39' if a < 40 else '40-49' if a < 50 else '50+'

    def audio(r):
        s = (m[r]['audio_device'] or '').lower()
        return 'Headphones' if any(k in s for k in
            ('headphone', 'headset', 'earphone', 'koptelefoon', 'px8', 'airpod')) else 'Speakers'

    cuts = [('Sex', lambda r: m[r]['sex']), ('Age', lambda r: band(m[r]['age'])),
            ('Residence', lambda r: m[r]['country_of_residence']),
            ('Employment', lambda r: m[r]['employment'] or 'not stated'),
            ('Audio', audio), ('System', lambda r: m[r]['os_name'])]
    sub('subgroups')
    print(f'  {"cut":<12}{"group":<24}{"n":>4}{"vs mean":>10}{"vs 2nd":>9}{"p":>9}')
    tested = []
    negatives = []
    for cut, fn in cuts:
        b = defaultdict(list)
        for r in per:
            b[fn(r)].append(r)
        for k, v in sorted(b.items(), key=lambda x: -len(x[1])):
            p_ = pv(v)
            mm = margin_mean(v)
            if mm < 0:
                negatives.append((cut, k, len(v), mm))
            if len(v) >= MIN_TESTABLE:
                tested.append(p_)
            if p_ is None or len(v) < MIN_TESTABLE:
                ps = '—'
            else:
                ps = '<0.001' if p_ < 0.001 else f'{p_:.3f}'
            print(f'  {cut:<12}{str(k)[:22]:<24}{len(v):>4}{mm:>+10.3f}{margin_second(v):>+9.3f}{ps:>9}')
    if negatives:
        print(f'\n  groups with a NEGATIVE margin: {len(negatives)}')
        for cut, k, nn, mm in negatives:
            print(f'    {cut:<12}{str(k)[:22]:<24}{nn:>4}{mm:>+10.3f}')
    else:
        print('\n  groups with a NEGATIVE margin: none')
    if not tested:
        print(f'  groups n>={MIN_TESTABLE}: 0')
    else:
        print(f'  groups n>={MIN_TESTABLE}: {len(tested)}   '
              f'p<0.05 within the group alone: '
              f'{sum(1 for p_ in tested if p_ is not None and p_ < 0.05)}')
    
def s_screening(d, per):
    head('SCREENING')
    byr = defaultdict(list)
    for x in d['ratings']:
        byr[x['rater_id']].append(x)
    prof = {}
    for r, rows in byr.items():
        flat = sum(1 for x in rows if len({x[a] for a in AXK if x[a] != ''}) == 1)
        durs, turns, nosess = [], [], 0
        for x in rows:
            j = d['join'].get(x['slate_id'])
            u = j['session_uuid'] if j else ''
            if not u or u not in d['sessions']:
                nosess += 1
                continue
            e = d['sessions'].get(u)
            if e and e['duration_seconds']:
                durs.append(float(e['duration_seconds']))
            t = d['turns'].get(u)
            if t and t['n_user_turns']:
                turns.append(int(t['n_user_turns']))
        prof[r] = dict(flat=flat / len(rows),
                       dur=st.median(durs) if durs else None,
                       turns=st.median(turns) if turns else None,
                       secs=st.median([int(x['completion_time_ms']) for x in rows
                                       if x['completion_time_ms']]) / 1000,
                       sd=st.pstdev([float(x[a]) for x in rows for a in AXK if x[a] != '']),
                       nosess=nosess, rated=len(rows))
    print(f'  median session seconds        {st.median([v["dur"] for v in prof.values() if v["dur"]]):.0f}')
    print(f'  median user turns             {st.median([v["turns"] for v in prof.values() if v["turns"]]):.0f}')
    print(f'  median seconds per rating     {st.median([v["secs"] for v in prof.values()]):.0f}')
    print(f'  median straight-lined share   {st.median([v["flat"] for v in prof.values()]):.0%}')
    print(f'  raters rating faster than 20s {sum(1 for v in prof.values() if v["secs"] < 20)}')
    flags = {
        'straight-liners': {r for r, v in prof.items() if v['flat'] >= 0.75},
        'short sessions': {r for r, v in prof.items() if v['dur'] is not None and v['dur'] < 30},
        'few turns': {r for r, v in prof.items() if v['turns'] is not None and v['turns'] <= 3},
        'missing telemetry': {r for r, v in prof.items()
                              if v['nosess'] >= v['rated'] / 2},
    }
    if any('browser_version' not in m for m in d['raters'].values()):
        print('  Device-fingerprint exclusion skipped: individual metadata is not published.')
    else:
        flags['never reached anam'] = never_reached_ids(d)
    sub('exclusion sensitivity')

    def lead(rs):
        means = {p: st.mean([per[r][p] for r in rs if p in per[r]]) for p in PROV}
        b = max((means[p], p) for p in PROV if p != REF)[1]
        return means[REF] - means[b]
    allr = set(per)
    print(f'  {"excluded":<26}{"n":>5}{"lead":>9}')
    print(f'  {"none (published)":<26}{len(allr):>5}{lead(allr):>+9.3f}')
    for name, s in flags.items():
        if s:
            print(f'  {name:<26}{len(allr-s):>5}{lead(allr-s):>+9.3f}   ({len(s)} flagged)')
    engagement = set().union(*[flags[k] for k in
                               ['straight-liners', 'short sessions', 'few turns', 'missing telemetry']])
    print(f'  {"all engagement screens":<26}{len(allr-engagement):>5}{lead(allr-engagement):>+9.3f}')
    if flags.get('never reached anam'):
        both = engagement | flags['never reached anam']
        print(f'  {"engagement + never reached":<26}{len(allr-both):>5}{lead(allr-both):>+9.3f}')
    sub('telemetry coverage by browser')
    cov = defaultdict(list)
    for r, v in prof.items():
        cov[d['raters'][r]['browser']].append(1 - v['nosess'] / v['rated'])
    for b, v in sorted(cov.items(), key=lambda x: -len(x[1])):
        print(f'  {b:<18} {st.mean(v)*100:>5.0f}%  ({len(v)} raters)')


def s_resolution(d):
    head('DELIVERED RESOLUTION')
    z, _ = normalise(d['ratings'])
    for p in PROV:
        v = [int(e['res_h_t15']) for e in d['sessions'].values()
             if arm_of(e) == p and e['res_h_t15']]
        xs, ys = [], []
        for x in d['ratings']:
            if x['provider'] != p:
                continue
            j = d['join'].get(x['slate_id'])
            e = d['sessions'].get(j['session_uuid']) if j else None
            zz = z.get(x['slate_id'])
            if e and e['res_h_t15'] and zz and 'visual_quality' in zz:
                xs.append(int(e['res_h_t15']))
                ys.append(zz['visual_quality'])
        if len(set(xs)) > 1:
            rr = f'r={pearson(xs, ys):+.3f}'
        else:
            rr = 'not defined (every session the same height)'
        print(f'  {p:<{PW}} median delivered height {st.median(v):>5.0f}px   '
              f'correlation with visual-quality rating {rr}')


# ---------------------------------------------------------------- envelopes
def spans(db, hz, thresh=THRESH, min_dur=MIN_DUR):
    out, start, on = [], 0, False
    for i, v in enumerate(db):
        loud = v > thresh
        if loud and not on:
            start, on = i, True
        elif not loud and on:
            if (i - start) / hz >= min_dur:
                out.append((start / hz, i / hz))
            on = False
    if on and (len(db) - start) / hz >= min_dur:
        out.append((start / hz, len(db) / hz))
    return out


def merge(sp, gap=MERGE_GAP):
    if not sp:
        return []
    out = [list(sp[0])]
    for s, e in sp[1:]:
        if s - out[-1][1] < gap:
            out[-1][1] = e
        else:
            out.append([s, e])
    return [tuple(x) for x in out]


def s_envelopes(bundle, d):
    head('RE-DERIVING TURN LATENCY FROM THE RAW AUDIO  (--check-envelopes)')
    path = bundle / 'audio_envelopes.jsonl.gz'
    if not path.exists():
        print('  audio_envelopes.jsonl.gz not present, skipping')
        return
    print(f'  threshold {THRESH} dBFS, min speech {MIN_DUR}s, turn merge {MERGE_GAP}s')
    agree = differ = 0
    with gzip.open(path, 'rt') as fh:
        for line in fh:
            e = json.loads(line)
            if e.get('status') != 'ok':
                continue
            hz = e['hz']
            mic = merge(spans(e['mic_db'], hz))
            av = merge(spans(e['avatar_db'], hz))
            lat = []
            for i, (us, ue) in enumerate(mic):
                if any(a <= ue < b for a, b in av):
                    continue                                   # barge-in, not a reply
                nxt = [a for a, _ in av if a > ue + 0.05]
                if not nxt:
                    continue
                a = min(nxt)
                if i + 1 < len(mic) and mic[i + 1][0] < a:
                    continue                                   # user kept talking
                if a - ue >= 0.1:
                    lat.append(round(a - ue, 3))
            pub = d['lat'].get(e['session_uuid'], [])
            if [round(x, 3) for x in pub] == lat:
                agree += 1
            else:
                differ += 1
    print(f'  sessions reproducing turn_latencies.csv exactly: {agree}')
    print(f'  sessions differing:                              {differ}')


# ---------------------------------------------------------------- main
FORCED_CONFIDENCE = ('exact', 'unique')


def restrict_to_unique_attribution(d):
    """Keep only rating->session pairs the evidence forces.

    'exact' pairs share a presentation id minted per stimulus presentation.
    'unique' pairs carry no presentation id and are forced by provider,
    topic, the rater's window AND the rater's device fingerprint. The join
    otherwise names a session whenever a plausible one exists, resolving ties
    by recency.
    """
    kept = 0
    for j in d['join'].values():
        if j.get('confidence') in FORCED_CONFIDENCE and j['session_uuid']:
            kept += 1
        else:
            j['session_uuid'] = ''
    total = len(d['ratings'])
    print(f'\n[--unique-attribution] keeping {kept} of {total} rating-session '
          f'pairs ({kept / total * 100:.0f}%); the rest are blanked.')
    print('  Analyses that do not pair a rating to a session are unaffected.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', default='evidence/x1ecy3l')
    ap.add_argument('--check-envelopes', action='store_true')
    ap.add_argument('--unique-attribution', action='store_true',
                    help='restrict rating-session pairs to unambiguous ones')
    a = ap.parse_args()
    bundle = Path(a.bundle)
    if not (bundle / 'ratings.csv').exists():
        sys.exit(f'no bundle at {bundle} (expected ratings.csv inside)')
    d = load(bundle)
    check_shape(d, bundle)
    if a.unique_attribution:
        restrict_to_unique_attribution(d)
    z, per = normalise(d['ratings'])
    print(f'Reproducing published figures from {bundle}')
    print(f'seed {SEED}, {BOOT:,} bootstrap resamples')
    s_dataset(d)
    s_headline(d, per)
    s_distribution(d)
    s_axes(d)
    s_avatars(d)
    s_scoring(d, per)
    s_connect(d)
    s_latency(d)
    s_perception(d)
    s_never_reached(d, per)
    s_pool(d, per)
    s_resolution(d)
    s_screening(d, per)
    if a.check_envelopes:
        s_envelopes(bundle, d)
    print('\ndone.')


if __name__ == '__main__':
    main()
