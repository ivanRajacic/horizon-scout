"""Build batchK's packet. Parent records are copied verbatim from the bank so
the drafters see exactly what is banked, not a transcription of it."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BANK = ROOT / "eval" / "bank.jsonl"

bank = {}
for line in BANK.read_text(encoding="utf-8").splitlines():
    if line.strip():
        r = json.loads(line)
        bank[r["question_id"]] = r

SLOTS = [
    ("adv-01", "sql", "false-presupposition", ["sql-04", "sql-01", "sql-02"]),
    ("adv-02", "vector", "zero-match", ["vec-15", "vec-04", "vec-01"]),
    ("adv-03", "hybrid", "data-absent", ["hyb-01", "hyb-03", "hyb-06"]),
]

packet = {
    "kind": "packet",
    "output_dir": "eval/drafts/batchK/",
    "order": "3 adversarial questions, one wearing each costume route "
             "(sql, vector, hybrid), each derived from a parent of that same "
             "route; ADV subtypes spread one per slot",
    "siblings": "none - this is the only tab in the run",
    "versions": {
        "corpus_profile": {"version": "cp8", "content_hash": "daea2b6899b9"},
        "schema_docs": {"version": "sd2", "content_hash": "f8c001e8cc8f"},
        "bank_brief": {"version": "bb4", "content_hash": "213f17b36728"},
        "index": {"fingerprint": "be84cbad9182", "n_vectors": 190248},
    },
    "slots": [
        {"question_id": qid,
         "cell": {"route": route, "level": "ADV", "subtype": subtype},
         "parents": [{"twin_id": p, "record": bank[p]} for p in parents]}
        for qid, route, subtype, parents in SLOTS
    ],
}

missing = [p for _, _, _, ps in SLOTS for p in ps if p not in bank]
if missing:
    raise SystemExit(f"parents not in the bank: {missing}")

out = Path(__file__).parent / "packet.json"
out.write_text(json.dumps(packet, indent=2), encoding="utf-8")
print(f"wrote {out} - {len(packet['slots'])} slots, "
      f"{sum(len(s['parents']) for s in packet['slots'])} parents")
for s in packet["slots"]:
    print(f"  {s['question_id']} {s['cell']['route']}/ADV/"
          f"{s['cell']['subtype']}: "
          + " -> ".join(p["twin_id"] for p in s["parents"]))
