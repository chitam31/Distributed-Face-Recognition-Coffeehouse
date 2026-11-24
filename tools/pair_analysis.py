"""Phân tích theo cặp trên embedding đã cache (results/.embeddings.npz) - bổ sung cho tools.eval_threshold.

Mỗi cặp ảnh được xếp vào "cùng người" hoặc "khác người". Cách nhìn này
  * cho phân bố độ giống thật, không phụ thuộc cách chia gallery / probe;
  * giúp chọn cặp ảnh enroll + nhận diện cho buổi demo (xem dòng "01 vs").

Chỉ cần numpy; chạy trong image tools mà không phải build lại (đọc mã từ stdin):

    docker compose run --rm -T --entrypoint python tools - < tools/pair_analysis.py

(chạy sau tools.eval_threshold để file cache đã có). Chỉ đọc cache, không ghi gì.
"""
import sys

import numpy as np

CACHE = sys.argv[1] if len(sys.argv) > 1 else "results/.embeddings.npz"
TAUS = [round(0.30 + 0.02 * i, 2) for i in range(16)]          # giống eval_threshold: 0.30 -> 0.60

z = np.load(CACHE, allow_pickle=False)
keys = sorted(k for k in z.files if z[k].size)                  # ảnh hỏng được cache dưới dạng rỗng
if len(keys) < 3:
    sys.exit("Cache có quá ít ảnh dùng được")
vec = np.stack([z[k] / np.linalg.norm(z[k]) for k in keys]).astype(np.float64)
parts = [k.replace("\\", "/").split("/") for k in keys]
who = [p[-2] for p in parts]
base = [p[-1] for p in parts]
S = vec @ vec.T                                                 # cosine (đã chuẩn hoá L2)
n = len(keys)
iu, ju = np.triu_indices(n, 1)
sim = S[iu, ju]
same = np.array([who[i] == who[j] for i, j in zip(iu, ju)])
gen, imp = sim[same], sim[~same]
if len(gen) == 0 or len(imp) == 0:
    sys.exit("Cần ít nhất 2 người và có người có từ 2 ảnh trở lên")


def pct(a, ps):
    return "  ".join(f"p{p}={np.percentile(a, p):.3f}" for p in ps)


print(f"{n} ảnh · {len(gen)} cặp cùng người · {len(imp)} cặp khác người")
print(f"cùng người : min={gen.min():.3f}  {pct(gen, (5, 25, 50))}  max={gen.max():.3f}")
print(f"khác người : {pct(imp, (50, 95, 99))}  max={imp.max():.3f}")
print("(các cặp dùng chung ảnh nên không độc lập; đây là mô tả, không phải khoảng tin cậy)\n")

print("ngưỡng   cặp cùng người bị bỏ sót     cặp khác người bị nhận nhầm")
for t in TAUS:
    miss = int((gen < t).sum())
    fa = int((imp >= t).sum())
    print(f"{t:>6.2f}   {miss:>4}/{len(gen)} = {miss / len(gen):>6.1%}        {fa:>5}/{len(imp)} = {fa / len(imp):>6.2%}")

print("\n5 cặp khác người giống nhau nhất:")
pi, pj = iu[~same], ju[~same]
for t in np.argsort(-imp)[:5]:
    print(f"  {who[pi[t]]}/{base[pi[t]]}  <->  {who[pj[t]]}/{base[pj[t]]}   {imp[t]:.3f}")

print("\nTừng người: số ảnh · độ giống giữa các ảnh của họ (trung bình / thấp nhất) · cặp tốt nhất · ảnh đầu tiên so với từng ảnh còn lại")
for p in sorted(set(who)):
    idx = [i for i in range(n) if who[i] == p]
    if len(idx) < 2:
        print(f"{p:<10} {len(idx)} ảnh")
        continue
    sub = S[np.ix_(idx, idx)]
    off = sub[np.triu_indices(len(idx), 1)]
    a, b = np.unravel_index(np.argmax(np.triu(sub, 1)), sub.shape)
    row = " ".join(f"{base[j].split('.')[0]}:{S[idx[0], j]:.2f}" for j in idx[1:])
    print(f"{p:<10} {len(idx):>2} ảnh · tb {off.mean():.2f} · min {off.min():.2f} · tốt nhất "
          f"{base[idx[a]].split('.')[0]}↔{base[idx[b]].split('.')[0]} {sub[a, b]:.2f} · {base[idx[0]].split('.')[0]} vs: {row}")
