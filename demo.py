"""Synthetic demo data (NOT a real researcher) so the app can be explored without an OpenAlex key.
Everything is generated locally with a fixed seed and flows through the same analysis code as real data."""
import random

import core

SURNAMES = ["Lindqvist", "Okafor", "Marchetti", "Nakamura", "Duarte", "Kowalski", "Haddad", "Petrov", "Larsen",
            "Mbeki", "Ferreira", "Tanaka", "Novak", "Silva", "Brandt", "Ivanova", "Chen", "Moreau", "Ahmed",
            "Rossi", "Kim", "Olsen", "Dubois", "Varga", "Santos", "Weber", "Park", "Costa", "Hughes", "Bianchi"]


def _name(i):
    return f"{chr(65 + i % 26)}. {SURNAMES[i % len(SURNAMES)]}"


TOPICS = ["graph models", "sparse signals", "language drift", "cell imaging", "network rewiring",
          "symbolic coding", "persona dynamics", "citation flow", "latent structure", "noise filtering"]


def _work(wid, title, year, authors, cited_by=0, refs=None, type_="article"):
    return {"id": wid, "title": title, "year": year, "cited_by": cited_by, "doi": None, "authors": authors,
            "type": type_, "subfield": "0000", "field": "00", "refs": refs or []}


def make_demo(seed=7):
    rng = random.Random(seed)
    me = ("ADEMO", "Demo Researcher")
    coauth = [(f"ACO{i}", _name(i * 5 + 1) + " (co-author)") for i in range(5)]
    friends = [(f"AFR{i}", _name(i * 3 + 20) + " (peer)") for i in range(2)]
    pool = [(f"AEX{i}", _name(i * 7)) for i in range(200)]       # authors who cite the demo researcher
    ref_pool = [(f"ARF{i}", _name(i * 11 + 3)) for i in range(200)]  # authors the demo researcher cites

    # reference works (what the demo researcher cites)
    ref_works = {}
    for j in range(120):
        au = rng.sample(ref_pool, rng.randint(1, 4))
        if j < 12:
            au = [friends[j % 2]] + au[:2]
        ref_works[f"RW{j}"] = _work(f"RW{j}", f"Prior work on {rng.choice(TOPICS)} #{j}",
                                    rng.randint(2000, 2020), au, rng.randint(5, 900))

    # the demo researcher's own works
    my = {}
    for i in range(1, 31):
        wid = f"WDEMO{i}"
        year = 2011 + (i * 12) // 30
        au = [me] + rng.sample(coauth, rng.randint(0, 2))
        refs = rng.sample(sorted(ref_works), rng.randint(15, 30))
        if i % 2 == 0:
            refs += [f"RW{rng.randint(0, 11)}" for _ in range(3)]  # friend-authored works
        refs += [f"WDEMO{j}" for j in rng.sample(range(1, i), min(2, i - 1))] if i > 3 and rng.random() < 0.6 else []
        my[wid] = _work(wid, f"Demo study {i}: {rng.choice(TOPICS)} in practice", year, au, 0, list(dict.fromkeys(refs)))

    # citing works
    ids = sorted(my)
    weights = [1 / (n + 1) ** 0.8 for n in range(len(ids))]
    citing = {}
    for n in range(260):
        k = rng.choices([1, 2, 3], [0.65, 0.25, 0.10])[0]
        hits = list(dict.fromkeys(rng.choices(ids, weights, k=k)))
        roll = rng.random()
        if roll < 0.10:
            au = [me] + rng.sample(pool, 2)
        elif roll < 0.16:
            au = [rng.choice(coauth)] + rng.sample(pool, 2)
        elif roll < 0.28:
            au = [rng.choice(friends)] + rng.sample(pool, rng.randint(1, 3))
        else:
            au = rng.sample(pool, rng.randint(2, 5))
        year = min(2025, max(my[h]["year"] for h in hits) + rng.randint(0, 5))
        citing[f"CW{n}"] = {**_work(f"CW{n}", f"Follow-up study {n}", year, au, rng.randint(0, 80)),
                            "hits": hits, "n_refs": max(rng.randint(20, 60), len(hits))}
        del citing[f"CW{n}"]["refs"]
    # two papers that cite many of the demo works
    for n, fr in zip((900, 901), friends):
        hits = rng.sample(ids, 6)
        citing[f"CW{n}"] = {**_work(f"CW{n}", f"Extended review {n}", 2023, [fr, rng.choice(pool)], 12),
                            "hits": hits, "n_refs": 40}
        del citing[f"CW{n}"]["refs"]

    for c in citing.values():
        for h in c["hits"]:
            my[h]["cited_by"] += 1

    refs = {**ref_works, **my}
    D = {"author_ids": [me[0]], "label": "Demo Researcher (synthetic)", "api_key": "", "my": my,
         "citing": citing, "refs": refs, "warnings": [], "demo": True}
    A = core.analyze(D)

    # synthetic matched baselines: peers in a field with weaker repeat-citer patterns
    eligible = [m for m, v in my.items() if v["cited_by"] >= core.MIN_CITING]
    per_work = []
    for m in eligible[:20]:
        tm = core.target_metrics(D, m)
        if tm is None:
            continue
        bms = []
        for _ in range(3):
            n = max(core.MIN_CITING + 2, my[m]["cited_by"] + rng.randint(-3, 3))
            cited = {f"B{m}"}
            draw = lambda k: {f"BX{rng.randrange(20000)}" for _ in range(k)}  # large author universe
            sets = [(cited if rng.random() < 0.05 else set()) | draw(rng.randint(2, 4)) for _ in range(n)]
            ref_au = draw(25)
            bm = core.paper_metrics(cited, sets, ref_au)
            if bm:
                bms.append(bm)
        if len(bms) >= 2:
            per_work.append({"id": m, "t": tm, "b": bms})
    B = core.summarize_benchmark(D, per_work, len(eligible), min(20, len(eligible)), len(per_work) * 3, seed)
    return D, A, B
