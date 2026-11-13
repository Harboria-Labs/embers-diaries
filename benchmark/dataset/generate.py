"""Deterministic synthetic dataset for Ember memory benchmarks.

Usage:
    python -m benchmark.dataset.generate --out-dir benchmark/dataset --scale 5000 --seed 20261113
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

FIRST = [
    "Mira", "Jonas", "Asha", "Nico", "Priya", "Leif", "Yara", "Omar", "Sable", "Kenji",
    "Elena", "Tariq", "Ines", "Bram", "Zola", "Hugo", "Nalini", "Felix", "Rhea", "Dario",
    "Soren", "Amira", "Pavel", "Chioma", "Lukas", "Hana", "Idris", "Noemi", "Viktor", "Aiko",
]
MID = [
    "Ell", "Mar", "Sol", "Venn", "Kade", "Rin", "Tess", "Olin", "Pax", "Shai",
    "Wren", "Nuri", "Cass", "Ivo", "Lumen", "Pere", "Quinn", "Azul", "Saffron", "Jory",
]
LAST = [
    "Chen", "Okoye", "Varga", "Nilsen", "Santos", "Berhe", "Kaur", "Dupont", "Ibrahim", "Novak",
    "Abebe", "Kowal", "Bergstrom", "Diallo", "Petrov", "Almeida", "Hassan", "Okafor", "Meyer", "Sato",
]
CITIES = [
    "Port Kell", "Amber Ridge", "South Lumen", "Kestrel Bay", "Iron Hollow",
    "Cape Verdia", "Dalmere", "New Saffron", "Orrin Reach", "Twin Harbors",
    "Gray Fen", "Solstice Quay", "Upper Mandrel", "Red Orchard", "Lake Ivo",
]
ORGS = [
    "Aurora Basin Lab", "Kestrel Hydronics", "Mandrel Foundry", "Verdia Transit",
    "Lumen Archives", "Hollow Grain Co", "Orrin Optics", "Fen Biosurvey",
    "Ridge Atomics", "Quay Cartography", "Ivo Ceramics", "Santos Cypress",
    "Diallo Timber", "Novak Current", "Sato Windworks",
]
PROFESSIONS = [
    "hydrochemist", "cartographer", "archivist", "metallurgist", "botanist",
    "navigator", "instrument maker", "epidemiologist", "bridge engineer", "conservator",
    "glaciologist", "signal officer", "ceramicist", "arborist", "windwright",
]
DISCOVERIES = [
    "Kell-ice isotope", "amber-ridge fault line", "lumen moth census", "hollow-grain rust",
    "verdia tide table", "orrin lens alloy", "fen peat horizon", "ivo kiln glaze",
    "saffron dye bind", "mandrel quench cycle",
]
AWARDS = [
    "Basin Merit", "Harbor Lantern", "Fen Field Medal", "Quay Chart Prize",
    "Ridge Service Star", "Cypress Leaf", "Current Ribbon", "Windworks Palm",
]
COUNTRIES = [
    "Kelland", "Verdia", "Orrania", "Fenmark", "Ivolia", "Mandrelia", "Saffria", "Ridgewald",
]
RIVERS = [
    "Kell River", "Lumen Run", "Fenwater", "Orrin Fork", "Ivo Reach", "Mandrel Brook",
    "Saffron Creek", "Ridgespill",
]
ADJECTIVES = [
    "North", "Hidden", "Copper", "Silent", "Third", "Outer", "Bright", "Late",
    "Second", "Inner", "Pale", "Eastern", "Winter", "Rapid", "Gentle",
]
NOUNS = [
    "Compass", "Ledger", "Kiln", "Harbor", "Lattice", "Garden", "Relay", "Cistern",
    "Atlas", "Foundry", "Observatory", "Depot", "Workshop", "Commons", "Mill",
]

MEMORY_TYPES = ["raw", "episodic", "connective", "reflective", "unscoped"]


def token(kind: str, index: int) -> str:
    raw = f"{kind}-{index}-20261113"
    digest = hashlib.sha1(raw.encode()).hexdigest()[:4].upper()
    return f"TOK{index:05d}{digest}"


def unique_name(rng: random.Random, i: int, used: set[str]) -> str:
    name = f"{FIRST[i % len(FIRST)]} {MID[(i * 3) % len(MID)]} {LAST[(i * 11) % len(LAST)]}"
    if name in used:
        name = f"{name} {i:03d}"
    used.add(name)
    return name


def unique_org(rng: random.Random, i: int, used: set[str]) -> str:
    name = f"{ADJECTIVES[i % len(ADJECTIVES)]} {NOUNS[(i * 5) % len(NOUNS)]} {ORGS[i % len(ORGS)].split()[-1]}"
    if name in used:
        name = f"{name}-{i:03d}"
    used.add(name)
    return name


def record(uid: str, index: int, kind: str, subject: str, content: str, *,
           tags: list[str], tok: str, answer: str, group: str, fact_key: str,
           related: list[str] | None = None, memory_type: str = "raw",
           extra: dict | None = None) -> dict:
    row = {
        "uid": uid,
        "index": index,
        "kind": kind,
        "subject": subject,
        "content": content,
        "tags": tags,
        "token": tok,
        "answer": answer,
        "group_id": group,
        "fact_key": fact_key,
        "related_uids": related or [],
        "memory_type": memory_type,
        "room": "task",
        "primary_context": None,
    }
    if extra:
        row.update(extra)
    return row


def generate(scale: int = 5000, seed: int = 20261113) -> tuple[list[dict], list[dict], dict]:
    rng = random.Random(seed)
    memories: list[dict] = []
    queries: list[dict] = []
    used_people: set[str] = set()
    used_orgs: set[str] = set()

    def uid(i: int) -> str:
        return f"m-{i:05d}"

    def add(row: dict) -> dict:
        memories.append(row)
        return row

    # ---- Block plan (write order = index, 0-based) ----
    # 0-799:     200 people x 4 facts
    # 800-899:   50 orgs x 2 facts
    # 900-949:   25 conflict pairs
    # 950-979:   30 supersede_v1
    # 980-999:   10 multi-hop chains (20 mems)
    # 1000-1799: 200 people x 4
    # 1800-1899: 50 orgs x 2
    # 1900-1949: 25 conflict pairs
    # 1950-1969: 20 near-dups of early facts
    # 1970-1999: 30 unicode / mixed-script facts
    # 2000-2499: 500 filler
    # 2500-3699: 300 people x 4
    # 3700-3899: 100 orgs x 2
    # 3900-3949: 25 conflict pairs
    # 3950-3999: 50 near-dups
    # 4000-4999: 1000 filler

    def people_block(start: int, n_people: int, people_offset: int) -> None:
        for p in range(n_people):
            i = people_offset + p
            name = unique_name(rng, i, used_people)
            city = CITIES[i % len(CITIES)]
            org = ORGS[i % len(ORGS)]
            prof = PROFESSIONS[i % len(PROFESSIONS)]
            disc = DISCOVERIES[i % len(DISCOVERIES)]
            award = AWARDS[i % len(AWARDS)]
            year = 1998 + (i % 25)
            badge_year = 2015 + (i % 10)
            base = start + p * 4
            group = f"person-{i:04d}"
            toks = [
                token("badge", base),
                token("disc", base + 1),
                token("home", base + 2),
                token("award", base + 3),
            ]
            facts = [
                (
                    "occupation",
                    f"{name} is a {prof} at {org} in {city}. Employee badge {toks[0]}.",
                    f"{prof} at {org}",
                    [prof, org, city, "occupation"],
                ),
                (
                    "discovery",
                    f"{name} discovered the {disc} in {year}. Lab notebook {toks[1]}.",
                    f"{disc} in {year}",
                    [disc, "discovery", str(year)],
                ),
                (
                    "residence",
                    f"{name} lives on {MID[i % len(MID)]} Lane in {city}. Residence file {toks[2]}.",
                    f"{MID[i % len(MID)]} Lane, {city}",
                    [city, "residence"],
                ),
                (
                    "award",
                    f"{name} received the {award} in {badge_year}. Citation {toks[3]}.",
                    f"{award} in {badge_year}",
                    [award, "award", str(badge_year)],
                ),
            ]
            for k, (fk, content, answer, tags) in enumerate(facts):
                idx = base + k
                tok = toks[k]
                add(record(
                    uid(idx), idx, "fact", name, content,
                    tags=tags, tok=tok, answer=answer, group=group, fact_key=fk,
                    memory_type="raw" if k % 2 == 0 else "episodic",
                ))
                # Exact-token query + paraphrase for a subset
                if p % 5 == 0 and k == 1:
                    queries.append({
                        "qid": f"q-exact-{idx:05d}",
                        "query": f"What does lab notebook {tok} refer to?",
                        "type": "exact_token",
                        "relevant_uids": [uid(idx)],
                        "min_index": idx,
                        "expected_answer": f"{name} discovered the {disc}",
                    })
                if p % 5 == 0 and k == 0:
                    queries.append({
                        "qid": f"q-para-{idx:05d}",
                        "query": f"Where does {name} work and what is their profession?",
                        "type": "paraphrase",
                        "relevant_uids": [uid(idx)],
                        "min_index": idx,
                        "expected_answer": answer,
                    })
                if p % 10 == 0 and k == 1:
                    queries.append({
                        "qid": f"q-disc-{idx:05d}",
                        "query": f"Who discovered the {disc} around {year} among the named researchers?",
                        "type": "paraphrase",
                        "relevant_uids": [uid(idx)],
                        "min_index": idx,
                        "expected_answer": name,
                    })

    def org_block(start: int, n_orgs: int, org_offset: int) -> None:
        for o in range(n_orgs):
            i = org_offset + o
            org = unique_org(rng, i, used_orgs)
            city = CITIES[(i * 2) % len(CITIES)]
            founded = 1960 + (i % 50)
            base = start + o * 2
            group = f"org-{i:04d}"
            t0, t1 = token("hq", base), token("found", base + 1)
            add(record(
                uid(base), base, "fact", org,
                f"{org} has its headquarters in {city}. Corporate filing {t0}.",
                tags=["headquarters", city, org], tok=t0, answer=city,
                group=group, fact_key="hq", memory_type="connective",
            ))
            add(record(
                uid(base + 1), base + 1, "fact", org,
                f"{org} was founded in {founded}. Charter {t1}.",
                tags=["founded", str(founded), org], tok=t1, answer=str(founded),
                group=group, fact_key="founded", memory_type="raw",
            ))
            if o % 4 == 0:
                queries.append({
                    "qid": f"q-hq-{base:05d}",
                    "query": f"Where is {org} headquartered?",
                    "type": "paraphrase",
                    "relevant_uids": [uid(base)],
                    "min_index": base,
                    "expected_answer": city,
                })

    def conflict_block(start: int, n_pairs: int, prefix: str) -> None:
        for c in range(n_pairs):
            base = start + c * 2
            subj = f"{prefix} subject {FIRST[c % len(FIRST)]} {LAST[(c * 13) % len(LAST)]}"
            year_a, year_b = 1972 + (c % 8), 1978 + (c % 8)
            t0, t1 = token("cfla", base), token("cflb", base + 1)
            add(record(
                uid(base), base, "conflict_a", subj,
                f"{subj}'s official birth year is recorded as {year_a}. Civil register {t0}.",
                tags=["birth", "conflict", str(year_a)], tok=t0, answer=str(year_a),
                group=f"conflict-{prefix}-{c}", fact_key="birth_a",
                related=[uid(base + 1)], extra={"conflict_pair": uid(base + 1), "polarity": "a"},
            ))
            add(record(
                uid(base + 1), base + 1, "conflict_b", subj,
                f"{subj}'s official birth year is recorded as {year_b}. Passport office {t1}.",
                tags=["birth", "conflict", str(year_b)], tok=t1, answer=str(year_b),
                group=f"conflict-{prefix}-{c}", fact_key="birth_b",
                related=[uid(base)], extra={"conflict_pair": uid(base), "polarity": "b"},
            ))
            queries.append({
                "qid": f"q-cfl-{base:05d}",
                "query": f"What is the official birth year of {subj}?",
                "type": "conflict",
                "relevant_uids": [uid(base), uid(base + 1)],
                "min_index": base + 1,
                "expected_answer": f"conflicting {year_a} vs {year_b}",
            })

    def supersede_block(start: int, n: int) -> None:
        for s in range(n):
            idx = start + s
            org = unique_org(rng, 8000 + s, used_orgs)
            city_a = CITIES[s % len(CITIES)]
            city_b = CITIES[(s + 5) % len(CITIES)]
            tok = token("sup", idx)
            add(record(
                uid(idx), idx, "supersede_v1", org,
                f"{org} headquarters is in {city_a}. Filing {tok}.",
                tags=["headquarters", city_a, "supersede"], tok=tok, answer=city_a,
                group=f"supersede-{s}", fact_key="hq_v1",
                extra={"update_to": f"{org} headquarters moved to {city_b} in 2025. Filing {tok}-V2.",
                       "update_answer": city_b, "update_city": city_b, "old_city": city_a},
            ))
            queries.append({
                "qid": f"q-sup-{idx:05d}",
                "query": f"Where is {org} headquartered now?",
                "type": "supersession",
                "relevant_uids": [uid(idx)],  # after update, current id may change; runner remaps
                "min_index": idx,
                "expected_answer": city_b,
                "needs_update": True,
                "must_not_prefer_old": city_a,
            })

    def multihop_block(start: int, n_chains: int) -> None:
        for h in range(n_chains):
            base = start + h * 2
            river = RIVERS[h % len(RIVERS)]
            # unique city/country pairing per chain
            city = f"{CITIES[h % len(CITIES)]} {ADJECTIVES[(h * 2) % len(ADJECTIVES)]} Crossing"
            country = COUNTRIES[h % len(COUNTRIES)]
            t0, t1 = token("riv", base), token("cap", base + 1)
            add(record(
                uid(base), base, "multihop", river,
                f"The {river} flows through {city}. Hydro survey {t0}.",
                tags=["river", city], tok=t0, answer=city,
                group=f"hop-{h}", fact_key="river_city",
                related=[uid(base + 1)],
            ))
            add(record(
                uid(base + 1), base + 1, "multihop", city,
                f"{city} is the capital of {country}. Gazette {t1}.",
                tags=["capital", country], tok=t1, answer=country,
                group=f"hop-{h}", fact_key="city_country",
                related=[uid(base)],
            ))
            queries.append({
                "qid": f"q-hop-{base:05d}",
                "query": f"Which country is associated with the {river}?",
                "type": "multihop",
                "relevant_uids": [uid(base), uid(base + 1)],
                "min_index": base + 1,
                "expected_answer": country,
                "hop_queries": [
                    f"Where does the {river} flow?",
                    f"Which country is {city} the capital of?",
                ],
            })

    def neardup_block(start: int, n: int, sources: list[dict]) -> None:
        picks = [m for m in sources if m["kind"] == "fact"]
        for d in range(n):
            idx = start + d
            src = picks[d % len(picks)]
            # Rephrase without the original token.
            content = (
                f"Restated note about {src['subject']}: {src['answer']}. "
                f"This is a near-duplicate of an earlier record. Cross-ref {token('dup', idx)}."
            )
            add(record(
                uid(idx), idx, "near_dup", src["subject"], content,
                tags=list(src["tags"]) + ["near-duplicate"],
                tok=token("dup", idx), answer=src["answer"],
                group=src["group_id"], fact_key=src["fact_key"] + "_dup",
                related=[src["uid"]], extra={"duplicate_of": src["uid"]},
            ))
            queries.append({
                "qid": f"q-dup-{idx:05d}",
                "query": f"What is recorded about {src['subject']} regarding {src['fact_key']}?",
                "type": "near_duplicate",
                "relevant_uids": [src["uid"], uid(idx)],
                "min_index": idx,
                "expected_answer": src["answer"],
            })

    def unicode_block(start: int, n: int) -> None:
        samples = [
            ("José García", "José García recibió el premio São Paulo en 2021. Acta {tok}."),
            ("田中 美咲", "田中美咲は神戸で光触媒を研究している。記録 {tok}。"),
            ("محمد علي", "يعمل محمد علي مهندس جسور في الإسكندرية. ملف {tok}."),
            ("Zoë Müller", "Zoë Müller curates the Göteborg textile archive. Folder {tok}."),
            ("François Côté", "François Côté mapped the Côte-Nord kelp beds. Cahier {tok}."),
        ]
        for u in range(n):
            idx = start + u
            subj, tmpl = samples[u % len(samples)]
            # disambiguate repeats
            subj_u = f"{subj} ({u+1})"
            tok = token("uni", idx)
            content = tmpl.replace("{tok}", tok).replace(subj, subj_u)
            add(record(
                uid(idx), idx, "unicode", subj_u, content,
                tags=["unicode", "international"], tok=tok, answer=subj_u,
                group=f"unicode-{u}", fact_key="bio", memory_type="raw",
            ))
            if u % 3 == 0:
                queries.append({
                    "qid": f"q-uni-{idx:05d}",
                    "query": f"What is recorded about {subj_u}?",
                    "type": "unicode",
                    "relevant_uids": [uid(idx)],
                    "min_index": idx,
                    "expected_answer": subj_u,
                })

    def filler_block(start: int, n: int, tag: str) -> None:
        for f in range(n):
            idx = start + f
            city = CITIES[f % len(CITIES)]
            noun = NOUNS[f % len(NOUNS)]
            adj = ADJECTIVES[f % len(ADJECTIVES)]
            tok = token("fil", idx)
            subj = f"{adj} {noun} inventory {idx}"
            content = (
                f"Inventory note {idx} from {city}: the {adj.lower()} {noun.lower()} "
                f"stocktake logged item {tok} with shelf {(f % 40) + 1}."
            )
            add(record(
                uid(idx), idx, "filler", subj, content,
                tags=["inventory", city, tag], tok=tok, answer=tok,
                group=f"filler-{tag}", fact_key="inventory", memory_type="unscoped",
            ))
            if f % 25 == 0:
                queries.append({
                    "qid": f"q-fil-{idx:05d}",
                    "query": f"What inventory item has code {tok}?",
                    "type": "exact_token",
                    "relevant_uids": [uid(idx)],
                    "min_index": idx,
                    "expected_answer": tok,
                })

    # Distractor queries: two similar people, query uses a distinguishing fact.
    def add_distractors() -> None:
        people = [m for m in memories if m["fact_key"] == "occupation" and m["kind"] == "fact"]
        for a, b in zip(people[0::12], people[1::12]):
            if a["subject"] == b["subject"]:
                continue
            queries.append({
                "qid": f"q-dist-{a['index']:05d}",
                "query": f"What is {a['subject']}'s profession? Not {b['subject']}.",
                "type": "distractor",
                "relevant_uids": [a["uid"]],
                "irrelevant_uids": [b["uid"]],
                "min_index": max(a["index"], b["index"]),
                "expected_answer": a["answer"],
            })

    people_block(0, 200, 0)
    org_block(800, 50, 0)
    conflict_block(900, 25, "early")
    supersede_block(950, 30)
    multihop_block(980, 10)
    assert len(memories) == 1000, len(memories)

    people_block(1000, 200, 200)
    org_block(1800, 50, 50)
    conflict_block(1900, 25, "mid")
    neardup_block(1950, 20, memories[:800])
    unicode_block(1970, 30)
    filler_block(2000, 500, "mid")
    assert len(memories) == 2500, len(memories)

    people_block(2500, 300, 400)
    org_block(3700, 100, 100)
    conflict_block(3900, 25, "late")
    neardup_block(3950, 50, memories[1000:1800])
    filler_block(4000, 1000, "late")
    assert len(memories) == 5000, len(memories)

    add_distractors()

    if scale < 5000:
        memories = [m for m in memories if m["index"] < scale]
        queries = [q for q in queries if q["min_index"] < scale]

    # Sort queries by min_index for stable files
    queries.sort(key=lambda q: (q["min_index"], q["qid"]))
    meta = {
        "seed": seed,
        "scale": scale,
        "n_memories": len(memories),
        "n_queries": len(queries),
        "query_types": sorted({q["type"] for q in queries}),
        "kinds": sorted({m["kind"] for m in memories}),
        "checkpoints": [c for c in (1000, 2500, 5000) if c <= scale],
        "notes": (
            "Deterministic fictional corpus. Unique TOK###### tokens support an exact-match "
            "ceiling. Paraphrase queries never include those tokens. Supersession queries "
            "expect the post-update city (runner must apply updates before scoring)."
        ),
    }
    return memories, queries, meta


def write_dataset(out_dir: Path, scale: int, seed: int) -> dict:
    memories, queries, meta = generate(scale=scale, seed=seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    mem_path = out_dir / "memories.jsonl"
    q_path = out_dir / "queries.jsonl"
    gt_path = out_dir / "ground_truth.json"
    with mem_path.open("w", encoding="utf-8") as fh:
        for m in memories:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    with q_path.open("w", encoding="utf-8") as fh:
        for q in queries:
            fh.write(json.dumps(q, ensure_ascii=False) + "\n")
    gt = {
        "meta": meta,
        "queries": [
            {
                "qid": q["qid"],
                "type": q["type"],
                "relevant_uids": q["relevant_uids"],
                "irrelevant_uids": q.get("irrelevant_uids", []),
                "min_index": q["min_index"],
                "expected_answer": q.get("expected_answer"),
                "needs_update": q.get("needs_update", False),
            }
            for q in queries
        ],
    }
    gt_path.write_text(json.dumps(gt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    meta_path = out_dir / "DATASET.md"
    meta_path.write_text(
        f"# Benchmark dataset\n\n"
        f"- Seed: {seed}\n"
        f"- Memories: {meta['n_memories']}\n"
        f"- Queries: {meta['n_queries']}\n"
        f"- Types: {', '.join(meta['query_types'])}\n"
        f"- Generated by `python -m benchmark.dataset.generate`\n"
        f"- Fictional; no real personal data.\n",
        encoding="utf-8",
    )
    return {"memories": str(mem_path), "queries": str(q_path), "ground_truth": str(gt_path), **meta}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="benchmark/dataset")
    ap.add_argument("--scale", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20261113)
    args = ap.parse_args()
    info = write_dataset(Path(args.out_dir), args.scale, args.seed)
    print(json.dumps({k: info[k] for k in info if k != "notes"}, indent=2))


if __name__ == "__main__":
    main()
