#!/usr/bin/env python3
"""Recompute the normalised-stack (LiveKit) figures.



    python3 analysis/reproduce_livekit.py
    python3 analysis/reproduce_livekit.py --default-stack <dir> --normalised-stack <dir>
"""
import argparse
import csv
import importlib.util
import math
import random
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('reproduce', ROOT / 'analysis' / 'reproduce.py')
reproduce = importlib.util.module_from_spec(_spec)
sys.modules['reproduce'] = reproduce
_spec.loader.exec_module(reproduce)

# Set by main() from the two bundles.
PROV: list[str] = []
# Width of the arm-name column, widened to the longest arm by main()/load().
PW: int = 12
# The Anam arm these bundles field. Arms are named `<vendor>-<model>`, so the
# literal name changes with the model under test; main() resolves it.
REF: str = ''
AX = reproduce.AX
AXK = reproduce.AXK
SEED = reproduce.SEED
BOOT = reproduce.BOOT
MIN_TESTABLE = reproduce.MIN_TESTABLE
_VENDOR_LABEL = {'anam': 'Anam', 'tavus': 'Tavus', 'lemonslice': 'LemonSlice',
                 'heygen': 'HeyGen', 'synthesia': 'Synthesia'}


def vendor(arm: str) -> str:
    return reproduce.vendor(arm)


def fielded(name: str) -> str:
    """The fielded arm belonging to one vendor."""
    return next((p for p in PROV if vendor(p) == name), name)


def label(arm: str) -> str:
    return _VENDOR_LABEL.get(vendor(arm), arm)


FIRST, SECOND = 'default stack', 'normalised stack'

head, sub, pct, pearson, paired = (reproduce.head, reproduce.sub, reproduce.pct,
                                   reproduce.pearson, reproduce.paired)


# ------------------------------------------------------------- statistics
def collected(d):
    """(first, last) collection date of a bundle, read from the data."""
    ds = sorted(e['recorded_at_utc'][:10] for e in d['sessions'].values()
                if e.get('recorded_at_utc'))
    return (ds[0], ds[-1]) if ds else ('unknown', 'unknown')


def lead_by_rater(ratings, axis):
    """rater_id -> Anam minus the mean of the other arms, raw -2..+2 scale."""
    per = defaultdict(lambda: defaultdict(list))
    for r in ratings:
        if r[axis] != '':
            per[r['rater_id']][r['provider']].append(float(r[axis]))
    return {rid: st.mean(d[REF]) - st.mean([st.mean(d[p]) for p in PROV if p != REF])
            for rid, d in per.items() if all(p in d for p in PROV)}


def lead_over_field(ratings, axis):
    """Per rater: Anam minus the mean of the other arms, raw -2..+2 scale."""
    return list(lead_by_rater(ratings, axis).values())


def norm_lead_by_rater(d, axis):
    """As lead_by_rater, but on the per-study rater-normalised scale."""
    z, _ = reproduce.normalise(d['ratings'])
    per = defaultdict(lambda: defaultdict(list))
    for r in d['ratings']:
        zz = z.get(r['slate_id'])
        if zz and axis in zz:
            per[r['rater_id']][r['provider']].append(zz[axis])
    return {rid: st.mean(v[REF]) - st.mean([st.mean(v[p]) for p in PROV if p != REF])
            for rid, v in per.items() if all(p in v for p in PROV)}




def composite_per_rater(d):
    _, per = reproduce.normalise(d['ratings'])
    return per


def distribution(ratings):
    c = {p: defaultdict(int) for p in PROV}
    for r in ratings:
        for a in AXK:
            if r[a] != '':
                c[r['provider']][int(float(r[a]))] += 1
    return c


def avatar_cells(ratings, z):
    cells = defaultdict(list)
    for r in ratings:
        zz = z.get(r['slate_id'])
        if zz:
            cells[(r['provider'], r['slot'])].append(st.mean([zz[a] for a in AXK if a in zz]))
    return {k: (st.mean(v), 1.96 * st.stdev(v) / math.sqrt(len(v)), len(v))
            for k, v in cells.items() if len(v) > 1}


def raw_mean(ratings, prov, exclude=frozenset()):
    v = [st.mean([float(x[a]) for a in AXK if x[a] != ''])
         for x in ratings if x['provider'] == prov and x['slate_id'] not in exclude]
    return st.mean(v) if v else float('nan')


def half(v):
    return 1.96 * st.stdev(v) / math.sqrt(len(v))


def predictors(d):
    """Objective predictors of the response-time rating, within provider.

    Cluster-bootstrapped over raters, as reproduce.s_perception.
    """
    z, _ = reproduce.normalise(d['ratings'])
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
        rec.append(dict(rater=x['rater_id'], prov=x['provider'],
                        connect=int(e['connect_ms']) / 1000,
                        med=st.median(L) if len(L) >= 3 else None,
                        slow=(sum(1 for v in L if v > 3) / len(L)) if len(L) >= 3 else None,
                        rt=zz.get('response_time')))

    def pooled(rows, key):
        xs, ys = [], []
        for p in PROV:
            g = [r for r in rows if r['prov'] == p
                 and r[key] is not None and r['rt'] is not None]
            if len(g) < 3:
                continue
            mf, my = st.mean([r[key] for r in g]), st.mean([r['rt'] for r in g])
            xs += [r[key] - mf for r in g]
            ys += [r['rt'] - my for r in g]
        return (pearson(xs, ys) if len(xs) >= 3 else None), len(xs)

    by_rater = defaultdict(list)
    for r_ in rec:
        by_rater[r_['rater']].append(r_)
    raters = list(by_rater)
    out = {}
    for key in ('slow', 'med', 'connect'):
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
        out[key] = (r, lo, hi, 2 * min(above, 1 - above), n)
    return out


def latency_pool(d):
    """Per provider turn latencies, filtered exactly as reproduce.s_latency."""
    good = {u: v for u, v in d['lat'].items() if len(v) >= 3}
    prov_of = {t['session_uuid']: reproduce.arm_of(t) for t in d['turns'].values()}
    by = defaultdict(list)
    for u, v in good.items():
        by[prov_of.get(u)].extend(v)
    return by


# ---------------------------------------------------------------- sections
def s_datasets(A, B):
    head('DATASETS')
    for tag, d in ((FIRST, A), (SECOND, B)):
        c0, c1 = collected(d)
        per = Counter(r['provider'] for r in d['ratings'])
        print(f'  {tag:<18} {len(d["ratings"]):>5,} ratings  {len(d["raters"]):>3} raters  '
              f'collected {c0} to {c1}  per provider {dict(per)}')


def s_headline(A, B):
    head('HEADLINE')
    for tag, d in ((SECOND, B), (FIRST, A)):
        per = composite_per_rater(d)
        raters = list(per)
        rnd = random.Random(SEED)
        means = {p: st.mean([per[r][p] for r in raters if p in per[r]]) for p in PROV}
        boots = {p: [] for p in PROV}
        first = Counter()
        for _ in range(BOOT):
            s = [rnd.choice(raters) for _ in raters]
            m = {p: st.mean([per[r][p] for r in s if p in per[r]]) for p in PROV}
            for p in PROV:
                boots[p].append(m[p])
            first[max(PROV, key=lambda p: m[p])] += 1
        print(f'  --- {tag}')
        print(f'    {"provider":<{PW}}{"composite":>11}{"95% CI":>22}{"vs Anam":>20}')
        for p in sorted(PROV, key=lambda p: -means[p]):
            b = sorted(boots[p])
            ci = f'{b[int(BOOT * .025)]:+.3f} to {b[int(BOOT * .975)]:+.3f}'
            if p == REF:
                gap = ''
            else:
                m, se, sig, pv, n = paired({r: per[r][REF] for r in raters if REF in per[r]},
                                           {r: per[r][p] for r in raters if p in per[r]})
                gap = f'{m:.3f} ({sig:.1f} sigma)'
            print(f'    {p:<{PW}}{means[p]:>+11.3f}{ci:>22}{gap:>20}')
        print(f'    P(Anam first) {first[REF] / BOOT:.4f}  ({first[REF]:,} of {BOOT:,} resamples)')


def s_axes(A, B):
    head('PER STATEMENT')
    for tag, d in ((SECOND, B), (FIRST, A)):
        print(f'  --- {tag}')
        for k, name in AX:
            per = reproduce.axis_per_rater(d['ratings'], k)
            means = {p: st.mean([per[r][p] for r in per if p in per[r]]) for p in PROV}
            second = max((p for p in PROV if p != REF), key=lambda p: means[p])
            m, se, sig, pv, n = paired({r: per[r][REF] for r in per if REF in per[r]},
                                       {r: per[r][second] for r in per if second in per[r]})
            print(f'    {name:<20}{m:>+8.3f} vs {second:<11} p={pv:.3f}')


def s_second(A, B):
    head('SECOND PLACE')
    for tag, d in ((SECOND, B), (FIRST, A)):
        per = composite_per_rater(d)
        raters = list(per)
        rnd = random.Random(SEED)
        second = Counter()
        for _ in range(BOOT):
            s = [rnd.choice(raters) for _ in raters]
            m = {p: st.mean([per[r][p] for r in s if p in per[r]]) for p in PROV}
            second[sorted(PROV, key=lambda p: -m[p])[1]] += 1
        runner_up, hits = second.most_common(1)[0]
        print(f'  {tag:<18} P({label(runner_up)} second) {hits / BOOT:.0%}')


def s_scoring(A, B):
    head('SCORING SENSITIVITY')
    for tag, d in ((SECOND, B), (FIRST, A)):
        per = composite_per_rater(d)
        means = {p: st.mean([per[r][p] for r in per if p in per[r]]) for p in PROV}
        second = max((p for p in PROV if p != REF), key=lambda p: means[p])
        _, _, nsig, _, _ = paired({r: per[r][REF] for r in per if REF in per[r]},
                                  {r: per[r][second] for r in per if second in per[r]})
        rawper = defaultdict(dict)
        for r in d['ratings']:
            rawper[r['rater_id']].setdefault(r['provider'], []).append(
                st.mean([float(r[a]) for a in AXK if r[a] != '']))
        rp = {k: {p: st.mean(v) for p, v in vv.items()} for k, vv in rawper.items()}
        _, _, rsig, _, _ = paired({r: rp[r][REF] for r in rp if REF in rp[r]},
                                  {r: rp[r][second] for r in rp if second in rp[r]})
        print(f'  {tag:<18} raw {rsig:.2f} sigma   normalised {nsig:.2f} sigma')


def s_avatars(A, B):
    head('PER AVATAR')
    zB, _ = reproduce.normalise(B['ratings'])
    zA, _ = reproduce.normalise(A['ratings'])
    cB, cA = avatar_cells(B['ratings'], zB), avatar_cells(A['ratings'], zA)
    ranked = sorted(((m, h, n, k) for k, (m, h, n) in cB.items()), reverse=True)
    print(f'  {SECOND}, all {len(ranked)}:')
    for i, (m, h, n, k) in enumerate(ranked, 1):
        p, slot = k
        print(f'   {i:>2}. {p:<{PW}} {slot}  {m:+.3f} ± {h:.3f}  (n={n})')
    leads = []
    for drop in sorted({k[1] for k in cB}):
        rows = [r for r in B['ratings'] if r['slot'] != drop]
        _, per = reproduce.normalise(rows)
        means = {p: st.mean([per[r][p] for r in per if p in per[r]]) for p in PROV}
        second = max((p for p in PROV if p != REF), key=lambda p: means[p])
        m, *_ = paired({r: per[r][REF] for r in per if REF in per[r]},
                       {r: per[r][second] for r in per if second in per[r]})
        leads.append(m)
    print(f'  leave-one-slot-out lead range: {min(leads):+.3f} to {max(leads):+.3f}')


def paired_gap(da, db):
    """Difference between the two studies, paired on the raters they share.

    The two panels are drawn from one pool and overlap, so the samples are
    not independent. The difference is taken within each shared rater.
    """
    shared = sorted(set(da) & set(db))
    d = [db[r] - da[r] for r in shared]
    if len(d) < 3:
        return None, None, None, len(shared)
    m = st.mean(d)
    se = st.stdev(d) / math.sqrt(len(d))
    if not se:
        return m, None, None, len(shared)
    return m, 1.96 * se, reproduce.p_from_t(m / se, len(d) - 1), len(shared)


def s_gap(A, B):
    head('PER-STATEMENT LEAD, BOTH STUDIES')
    rows = []
    for k, name in AX:
        da, db = lead_by_rater(A['ratings'], k), lead_by_rater(B['ratings'], k)
        a, b = list(da.values()), list(db.values())
        diff, hd, pv, n = paired_gap(da, db)
        rows.append((name, st.mean(a), half(a), st.mean(b), half(b), diff, hd, pv, n))
    shared = rows[0][8]
    print(f'  Paired on the {shared} raters who took part in both studies; the two '
          f'panels are drawn from one pool and are not independent samples.')
    print(f'  {"statement":<20}{FIRST:>16}{SECOND:>17}{"difference":>15}{"p":>8}')
    for name, ma, ha, mb, hb, diff, hd, pv, n in sorted(rows, key=lambda r: -r[5]):
        print(f'  {name:<20}{ma:>+9.3f} ±{ha:.3f}{mb:>+9.3f} ±{hb:.3f}'
              f'{diff:>+9.3f} ±{hd:.3f}{pv:>8.3f}')
    na, nb = norm_lead_by_rater(A, 'lip_sync_quality'), norm_lead_by_rater(B, 'lip_sync_quality')
    diff, hd, pv, _ = paired_gap(na, nb)
    print(f'  {"Lip sync, normalised":<20}{st.mean(list(na.values())):>+9.3f} '
          f'±{half(list(na.values())):.3f}{st.mean(list(nb.values())):>+9.3f} '
          f'±{half(list(nb.values())):.3f}{diff:>+9.3f} ±{hd:.3f}{pv:>8.3f}')
    sig = [r for r in rows if r[7] < 0.05]
    bonf = sum(1 for r in rows if r[7] < 0.05 / len(rows))
    print(f'  significant: {len(sig)} of {len(rows)} ({", ".join(r[0] for r in sig) or "none"})   '
          f'Bonferroni bar p<{0.05 / len(rows):.4f}: {bonf} clear it')


def s_lipsync_movement(A, B):
    head('PER PROVIDER DIFFERENCE')
    deltas = {}
    for p in PROV:
        a = [float(x['lip_sync_quality']) for x in A['ratings']
             if x['provider'] == p and x['lip_sync_quality'] != '']
        b = [float(x['lip_sync_quality']) for x in B['ratings']
             if x['provider'] == p and x['lip_sync_quality'] != '']
        deltas[p] = st.mean(b) - st.mean(a)
        print(f'  {p:<{PW}} raw lip sync  {FIRST} {st.mean(a):+.2f}  {SECOND} {st.mean(b):+.2f}'
              f'   difference {deltas[p]:+.2f}')
    mags = sorted((abs(v) for v in deltas.values()), reverse=True)
    print(f'  largest delta / next largest: {mags[0]:.2f} / {mags[1]:.2f} = {mags[0] / mags[1]:.1f}x')

    sub('telemetry sweep: anam lip-sync rating (normalised) vs everything instrumented')
    z, _ = reproduce.normalise(B['ratings'])
    rec = []
    for x in B['ratings']:
        if x['provider'] != REF:
            continue
        j = B['join'].get(x['slate_id'])
        zz = z.get(x['slate_id'])
        if not j or not j['session_uuid'] or not zz or 'lip_sync_quality' not in zz:
            continue
        e = B['sessions'].get(j['session_uuid'])
        t = B['turns'].get(j['session_uuid'])
        L = B['lat'].get(j['session_uuid'], [])

        def f(row, k):
            v = row.get(k, '') if row else ''
            try:
                return float(v)
            except ValueError:
                return None
        rec.append(dict(ls=zz['lip_sync_quality'], res=f(e, 'res_h_t15'), fps=f(e, 'fps_t15'),
                        conn=f(e, 'connect_ms'), dur=f(e, 'duration_seconds'),
                        med=st.median(L) if len(L) >= 3 else None,
                        turns=f(t, 'n_user_turns'), talk=f(t, 'avatar_talk_s')))
    mx = 0.0
    for k, lab in (('res', 'delivered resolution'), ('fps', 'frame rate'),
                   ('conn', 'connection time'), ('dur', 'session duration'),
                   ('med', 'measured latency'), ('turns', 'turn count'),
                   ('talk', 'avatar talk time')):
        xs = [(r[k], r['ls']) for r in rec if r[k] is not None]
        r_ = pearson([a for a, _ in xs], [b for _, b in xs])
        mx = max(mx, abs(r_))
        print(f'    {lab:<22} r={r_:+.3f}')
    print(f'    max |r| = {mx:.3f}')


def s_voice(B):
    head('VOICE')
    man = {m['session_uuid']: m for m in
           csv.DictReader(open(Path(B['bundle']) / 'server_manifests.csv'))}
    per = defaultdict(lambda: defaultdict(list))
    for x in B['ratings']:
        j = B['join'].get(x['slate_id'])
        if not j or not j['session_uuid'] or x['lip_sync_quality'] == '':
            continue
        m = man.get(j['session_uuid'])
        if m:
            per[x['provider']][m['voiceName']].append(float(x['lip_sync_quality']))
    comp_means = {}
    print(f'  {"provider":<{PW}}{"voices":>7}{"mean":>7}{"sd(voice means)":>17}{"worst..best":>17}{"n/voice":>9}')
    for p in [REF] + [q for q in PROV if q != REF]:
        vm = {v: st.mean(xs) for v, xs in per[p].items()}
        ns = [len(xs) for xs in per[p].values()]
        allx = [x for xs in per[p].values() for x in xs]
        comp_means[p] = st.mean(allx)
        print(f'  {p:<{PW}}{len(vm):>7}{st.mean(allx):>+7.2f}{st.pstdev(list(vm.values())):>17.3f}'
              f'{min(vm.values()):>+8.2f}..{max(vm.values()):+.2f}{min(ns):>5}-{max(ns)}')

    # The slot and voice this study's two casts share.
    OVERLAP_SLOT, OVERLAP_VOICE = 'm2', 'Archie'
    sub(f'default-pairing overlap (the Anam {OVERLAP_SLOT} voice is also in the LiveKit male pool)')
    rated = [x for x in B['ratings'] if x['provider'] == REF and x['lip_sync_quality'] != '']
    ov = set()
    for x in rated:
        j = B['join'].get(x['slate_id'])
        m = man.get(j['session_uuid']) if j and j['session_uuid'] else None
        if m and x['slot'] == OVERLAP_SLOT and m['voiceName'].startswith(OVERLAP_VOICE):
            ov.add(x['slate_id'])
    def gap(excl):
        per_ = defaultdict(lambda: defaultdict(list))
        for x in B['ratings']:
            if x['lip_sync_quality'] == '' or x['slate_id'] in excl:
                continue
            per_[x['rater_id']][x['provider']].append(float(x['lip_sync_quality']))
        v = [st.mean(d[REF]) - st.mean([st.mean(d[p]) for p in PROV if p != REF])
             for d in per_.values() if all(p in d for p in PROV)]
        return st.mean(v)
    print(f'  overlap sessions {len(ov)} of {len(rated)} = {100 * len(ov) / len(rated):.1f}%   '
          f'gap all {gap(set()):+.3f}, excluding them {gap(ov):+.3f} (delta {gap(set()) - gap(ov):+.3f})')


def s_shape(B):
    head('SHAPE OF THE RATINGS')
    c = distribution(B['ratings'])
    print(f'  {"provider":<{PW}}' + ''.join(f'{v:>6}' for v in ('-2', '-1', '0', '+1', '+2')) + f'{"negative":>10}')
    for p in PROV:
        tot = sum(c[p].values())
        row = ''.join(f'{100 * c[p][v] / tot:>5.0f}%' for v in (-2, -1, 0, 1, 2))
        print(f'  {p:<{PW}}{row}{100 * (c[p][-2] + c[p][-1]) / tot:>9.1f}%')
    print(f'  statement scores per provider: {sum(c[PROV[0]].values()):,}')


def s_connect(A, B):
    head('CONNECTING')
    for tag, d in ((SECOND, B), (FIRST, A)):
        print(f'  --- {tag}')
        print(f'    {"provider":<{PW}}' + ''.join(f'{f"p{q}":>9}' for q in (50, 75, 90, 95)))
        for p in PROV:
            v = [int(e['connect_ms']) / 1000 for e in d['sessions'].values()
                 if reproduce.arm_of(e) == p and e['connect_ms']
                 and e['topic'] != d['practice_topic']]
            if v:
                print(f'    {p:<{PW}}' + ''.join(f'{pct(v, q):>8.1f}s' for q in (50, 75, 90, 95)))
        per = defaultdict(dict)
        for x in d['ratings']:
            j = d['join'].get(x['slate_id'])
            if not j or not j['session_uuid']:
                continue
            e = d['sessions'].get(j['session_uuid'])
            if e and e['connect_ms']:
                per[x['rater_id']].setdefault(x['provider'], []).append(int(e['connect_ms']))
        full = {r: {p: st.mean(v) for p, v in dd.items()} for r, dd in per.items() if len(dd) == len(PROV)}
        wins = Counter(min(v, key=v.get) for v in full.values())
        print(f'    fastest per rater, of {len(full)} with all {len(PROV)}: ' +
              ', '.join(f'{p} {wins[p]}' for p, _ in wins.most_common()))


def s_responding(A, B):
    head('RESPONDING')
    for tag, d in ((SECOND, B), (FIRST, A)):
        by = latency_pool(d)
        print(f'  --- {tag}')
        print(f'    {"provider":<{PW}}{"n":>7}' + ''.join(f'{f"p{q}":>8}' for q in (50, 75, 90, 95)) + f'{">4s":>8}')
        for p in sorted(PROV, key=lambda p: pct(by[p], 50)):
            v = by[p]
            over4 = 100 * sum(1 for x in v if x > 4) / len(v)
            print(f'    {p:<{PW}}{len(v):>7}' + ''.join(f'{pct(v, q):>7.2f}s' for q in (50, 75, 90, 95))
                  + f'{over4:>7.1f}%')
    sub('paired within rater, session-median unit (normalised stack)')
    good = {u: st.median(v) for u, v in B['lat'].items() if len(v) >= 3}
    per = defaultdict(lambda: defaultdict(list))
    for x in B['ratings']:
        j = B['join'].get(x['slate_id'])
        if j and j['session_uuid'] in good:
            per[x['rater_id']][x['provider']].append(good[j['session_uuid']])
    agg = {p: {r: st.mean(v[p]) for r, v in per.items() if p in v} for p in PROV}
    for p in PROV:
        if p == REF:
            continue
        m, se, sig, pv, n = paired(agg[REF], agg[p])
        print(f'  Anam minus {p:<{PW}}{m:>+8.3f}s ({sig:+.1f} sigma, n={n} raters)')


def s_perception(A, B):
    head('RATINGS AGAINST TELEMETRY')
    names = {'slow': 'share of turns over 3 s', 'med': 'median turn latency',
             'connect': 'connection time'}
    pa, pb = predictors(A), predictors(B)
    for key in ('slow', 'med', 'connect'):
        print(f'  {names[key]}')
        for tag, out in ((SECOND, pb), (FIRST, pa)):
            r, lo, hi, pv, n = out[key]
            print(f'    {tag:<18}r={r:+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]  p={pv:.3f}  n={n}')
    sub('halo: correlation between any two statements within one conversation')
    for tag, d in ((SECOND, B), (FIRST, A)):
        z, _ = reproduce.normalise(d['ratings'])
        full = [z[s] for s in z if len(z[s]) == 6]
        offs, vq = [], None
        for i, (a_, _) in enumerate(AX):
            for b_, _ in AX[i + 1:]:
                r = pearson([v[a_] for v in full], [v[b_] for v in full])
                offs.append(r)
                if {a_, b_} == {'visual_quality', 'response_time'}:
                    vq = r
        print(f'  {tag:<18}mean r={st.mean(offs):+.3f}   visual quality vs response time {vq:+.3f}')


def s_resolution(A, B):
    head('DELIVERED RESOLUTION')
    for tag, d in ((SECOND, B), (FIRST, A)):
        z, _ = reproduce.normalise(d['ratings'])
        print(f'  --- {tag}')
        for p in PROV:
            xs, ys = [], []
            for x in d['ratings']:
                if x['provider'] != p:
                    continue
                j = d['join'].get(x['slate_id'])
                e = d['sessions'].get(j['session_uuid']) if j and j['session_uuid'] else None
                zz = z.get(x['slate_id'])
                if not e or not zz or not e['res_h_t15'] or 'visual_quality' not in zz:
                    continue
                xs.append(int(e['res_h_t15']))
                ys.append(zz['visual_quality'])
            rr = 'undefined (one height)' if len(set(xs)) <= 1 else f'r={pearson(xs, ys):+.3f}'
            print(f'    {p:<{PW}}{rr}')


def s_subgroups(A, B):
    head('SUBGROUPS')
    for tag, d in ((SECOND, B), (FIRST, A)):
        per = composite_per_rater(d)
        m = d['raters']

        def margin_mean(rs):
            return st.mean([per[r][REF] - st.mean([per[r][q] for q in PROV if q != REF])
                            for r in rs])

        def margin_second(rs):
            means = {q: st.mean([per[r][q] for r in rs]) for q in PROV if q != REF}
            b = max(means, key=means.get)
            return st.mean([per[r][REF] - per[r][b] for r in rs])

        def pv_ci(rs):
            dd = [per[r][REF] - st.mean([per[r][q] for q in PROV if q != REF]) for r in rs]
            if len(dd) < 2:
                return None, None
            se = st.stdev(dd) / math.sqrt(len(dd))
            return (reproduce.p_from_t(st.mean(dd) / se, len(dd) - 1) if se else None,
                    (st.mean(dd) - 1.96 * se, st.mean(dd) + 1.96 * se))

        def band(a):
            # No age given.
            if not a:
                return 'not stated'
            if a in ('18-29', '30-39', '40-49', '50+'):
                return a
            a = int(a)
            return '18-29' if a < 30 else '30-39' if a < 40 else '40-49' if a < 50 else '50+'

        def audio(r):
            s = (m[r]['audio_device'] or '').lower()
            return 'Headphones' if any(k in s for k in
                ('headphone', 'headset', 'earphone', 'koptelefoon', 'px8', 'airpod')) else 'Speakers'

        cuts = [('Sex', lambda r: m[r]['sex'] or 'not stated'),
                ('Age', lambda r: band(m[r]['age'])),
                ('Residence', lambda r: m[r]['country_of_residence'] or 'not stated'),
                ('Employment', lambda r: m[r]['employment'] or 'not stated'),
                ('Audio', audio), ('System', lambda r: m[r]['os_name'])]
        tested, results, margins2 = 0, [], []
        favour = sig_count = 0
        for cut, fn in cuts:
            b_ = defaultdict(list)
            for r in per:
                b_[fn(r)].append(r)
            for k, v in b_.items():
                if len(v) < MIN_TESTABLE:
                    continue
                tested += 1
                pv, ci = pv_ci(v)
                mm = margin_mean(v)
                m2 = margin_second(v)
                margins2.append(m2)
                if mm > 0:
                    favour += 1
                if pv is not None and pv < 0.05:
                    sig_count += 1
                results.append((cut, k, len(v), pv, ci))
        fails = [r for r in results if r[3] is None or r[3] >= 0.05]
        print(f'  {tag:<18} groups n>={MIN_TESTABLE}: {tested}  positive margin: {favour}  '
              f'p<0.05 within the group alone: {sig_count}')
        for cut, k, n, pv, ci in fails:
            excl = 'CI excludes zero' if ci and ci[0] > 0 else 'CI includes zero'
            ps = 'undefined' if pv is None else f'{pv:.3f}'
            print(f'    misses correction: {cut}/{k}  n={n}  p={ps}  ({excl})')
        if margins2:
            print(f'    margin vs second place across groups: '
                  f'{min(margins2):+.3f} to {max(margins2):+.3f}')


def s_screening(B):
    head('EXCLUSION SCREENS')
    d = B
    per = composite_per_rater(d)
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
        prof[r] = dict(flat=flat / len(rows), dur=st.median(durs) if durs else None,
                       turns=st.median(turns) if turns else None, nosess=nosess,
                       rated=len(rows))
    flags = {
        'straight-liners': {r for r, v in prof.items() if v['flat'] >= 0.75},
        'short sessions': {r for r, v in prof.items() if v['dur'] is not None and v['dur'] < 30},
        'few turns': {r for r, v in prof.items() if v['turns'] is not None and v['turns'] <= 3},
        'missing telemetry': {r for r, v in prof.items()
                              if v['nosess'] >= v['rated'] / 2},
    }

    def lead(rs):
        means = {p: st.mean([per[r][p] for r in rs if p in per[r]]) for p in PROV}
        b = max((means[p], p) for p in PROV if p != REF)[1]
        return means[REF] - means[b]
    allr = set(per)
    print(f'  {"excluded":<26}{"n":>5}{"lead":>9}')
    print(f'  {"none (published)":<26}{len(allr):>5}{lead(allr):>+9.3f}')
    for name, s in flags.items():
        if s:
            print(f'  {name:<26}{len(allr - s):>5}{lead(allr - s):>+9.3f}   ({len(s)} flagged)')
    engagement = set().union(*flags.values())
    print(f'  {"all engagement screens":<26}{len(allr - engagement):>5}{lead(allr - engagement):>+9.3f}')


def s_silent(B):
    head('CONVERSATIONS WITH NO RATER TURN')
    silent = []
    for x in B['ratings']:
        j = B['join'].get(x['slate_id'])
        tm = B['turns'].get(j['session_uuid']) if j and j['session_uuid'] else None
        if tm is None:
            continue
        if tm['n_user_turns'] != '' and int(float(tm['n_user_turns'])) == 0:
            silent.append(x)
    n = len(B['ratings'])
    print(f'  rater never spoke: {len(silent)} of {n:,} ({100 * len(silent) / n:.1f}%)   '
          f'by provider {dict(Counter(x["provider"] for x in silent))}')
    for p in PROV:
        s = [st.mean([float(x[a]) for a in AXK if x[a] != '']) for x in silent if x['provider'] == p]
        if s:
            print(f'    {p:<{PW}}silent mean {st.mean(s):+.3f}  vs all {raw_mean(B["ratings"], p):+.3f}  '
                  f'diff {st.mean(s) - raw_mean(B["ratings"], p):+.3f}')
    ex = {x['slate_id'] for x in silent}
    rival = fielded('tavus')
    all_lead = raw_mean(B['ratings'], REF) - raw_mean(B['ratings'], rival)
    kept_lead = raw_mean(B['ratings'], REF, ex) - raw_mean(B['ratings'], rival, ex)
    print(f'  raw Anam lead over {label(rival)}: all {all_lead:+.3f}, '
          f'excluding silent {kept_lead:+.3f}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--default-stack', default='evidence/x1ecy3l',
                    help='bundle for the study where each provider ran its own stack')
    ap.add_argument('--normalised-stack', default='evidence/x11dvlb',
                    help='bundle for the study where one pipeline was shared')
    a = ap.parse_args()
    pa, pb = Path(a.default_stack), Path(a.normalised_stack)
    for p in (pa, pb):
        if not (p / 'ratings.csv').exists():
            sys.exit(f'no bundle at {p} (expected ratings.csv inside)')
    A, B = reproduce.load(pa), reproduce.load(pb)
    A['bundle'], B['bundle'] = str(pa), str(pb)
    # The arms both studies fielded. A comparison can only speak about those.
    global PROV, REF, PW
    PROV = sorted({r['provider'] for r in A['ratings']}
                  & {r['provider'] for r in B['ratings']})
    REF = fielded('anam')
    PW = max((len(q) for q in PROV), default=10) + 2
    if not PROV:
        sys.exit(f'{pa} and {pb} share no arm, so there is nothing to compare')
    only_a = sorted({r['provider'] for r in A['ratings']} - set(PROV))
    only_b = sorted({r['provider'] for r in B['ratings']} - set(PROV))
    print('Reproducing the normalised-stack figures')
    print(f'  {FIRST:<18}{pa}')
    print(f'  {SECOND:<18}{pb}')
    print(f'  seed {SEED}, {BOOT:,} bootstrap resamples')
    # Every figure below is computed over PROV, the arms both studies fielded.
    # An arm present in one study only is named here and takes no further part:
    # a provider that ships no speech-and-language stack of its own has no
    # own-stack arm to compare against, and the gap between the two studies is
    # undefined for it.
    if only_a or only_b:
        print(f'  compared arms     {", ".join(PROV)}')
        for bundle, extra, d in ((pa, only_a, A), (pb, only_b, B)):
            for arm in extra:
                n = sum(1 for r in d['ratings'] if r['provider'] == arm)
                print(f'  {bundle} only  {arm}  ({n:,} ratings, excluded below)')
    # Every figure below reads these two lists, so the restriction is applied
    # once here rather than at each use: an arm only one study fielded has no
    # counterpart to be compared against.
    A['ratings'] = [r for r in A['ratings'] if r['provider'] in PROV]
    B['ratings'] = [r for r in B['ratings'] if r['provider'] in PROV]
    s_datasets(A, B)
    s_headline(A, B)
    s_axes(A, B)
    s_second(A, B)
    s_scoring(A, B)
    s_avatars(A, B)
    s_gap(A, B)
    s_lipsync_movement(A, B)
    s_voice(B)
    s_shape(B)
    s_connect(A, B)
    s_responding(A, B)
    s_perception(A, B)
    s_resolution(A, B)
    s_subgroups(A, B)
    s_screening(B)
    s_silent(B)
    print('\ndone.')


if __name__ == '__main__':
    main()
