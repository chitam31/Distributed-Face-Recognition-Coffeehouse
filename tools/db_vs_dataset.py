"""Chỉ đọc: so từng embedding trong CSDL với ảnh dataset đã có nhãn (tên thư mục), không in ảnh nào."""
import json

import numpy as np
import psycopg

from common.config import load_settings

z = np.load("results/.embeddings.npz", allow_pickle=False)
keys = sorted(k for k in z.files if z[k].size)
ref = np.stack([z[k] / np.linalg.norm(z[k]) for k in keys]).astype(np.float64)
who = [k.replace("\\", "/").split("/")[-2] for k in keys]
people = sorted(set(who))

with psycopg.connect(load_settings().pg_dsn) as conn:
    rows = conn.execute("SELECT embedding_id, customer_id, embedding::text FROM face_embeddings "
                        "ORDER BY customer_id, embedding_id").fetchall()

print(f"{len(keys)} ảnh dataset, {len(people)} người; {len(rows)} embedding trong CSDL")
print("embedding  khách | 3 người trong dataset giống nhất (độ giống cao nhất với ảnh của người đó)")
for eid, cid, txt in rows:
    e = np.array(json.loads(txt), dtype=np.float64)
    e /= np.linalg.norm(e)
    sims = ref @ e
    best = {p: max(sims[i] for i in range(len(who)) if who[i] == p) for p in people}
    top = sorted(best.items(), key=lambda kv: -kv[1])[:3]
    print(f"{eid:>9}  #{cid:<4} | " + "   ".join(f"{p} {v:.3f}" for p, v in top))
