"""Test các hàm đo đạc và dọn dữ liệu (tools/metrics.py) và smoke test của make_charts."""
import json
import os
import tempfile
import unittest

import numpy as np

from tests.fakes import noisy, unit
from tools.amqp_peek import peek_queue
from tools.metrics import best_match, diff_ids, frange, percentile, pick_threshold, summarize_latencies, sweep_thresholds


class PercentileTests(unittest.TestCase):
    def test_matches_numpy(self):
        data = [12.0, 5.0, 33.0, 8.0, 21.0, 40.0, 3.0, 17.0, 29.0, 9.0]
        for p in (0, 25, 50, 90, 95, 99, 100):
            self.assertAlmostEqual(percentile(data, p), float(np.percentile(data, p)), places=9)

    def test_edge_cases(self):
        self.assertTrue(np.isnan(percentile([], 50)))
        self.assertEqual(percentile([7.0], 95), 7.0)

    def test_summary(self):
        s = summarize_latencies([100, 200, 300, 400])
        self.assertEqual(s["count"], 4)
        self.assertEqual(s["p50_ms"], 250.0)
        self.assertEqual(summarize_latencies([]), {"count": 0})


class SweepTests(unittest.TestCase):
    def setUp(self):
        # 3 người trong gallery, 2 người lạ. Cùng một người: nhiễu nhỏ -> sim cao; người khác: ~0.
        self.people = {f"p{i}": unit(i) for i in range(3)}
        self.gallery = {n: np.stack([v]) for n, v in self.people.items()}
        self.probes = []
        for i, (n, v) in enumerate(self.people.items()):
            for k in range(4):
                self.probes.append((n, noisy(v, 0.015, 100 * i + k)))
        self.probes += [(None, unit(900)), (None, unit(901))]

    def test_best_match_picks_right_person(self):
        name, sim = best_match(self.gallery, noisy(self.people["p1"], 0.015, 5))
        self.assertEqual(name, "p1")
        self.assertGreater(sim, 0.9)

    def test_low_threshold_accepts_strangers_high_threshold_misses_known(self):
        rows = {r["threshold"]: r for r in sweep_thresholds(self.gallery, self.probes, [0.3, 0.5, 0.99])}
        self.assertEqual(rows[0.5]["correct_rate"], 1.0)
        self.assertEqual(rows[0.5]["far"], 0.0)
        self.assertEqual(rows[0.5]["decision_acc"], 1.0)
        self.assertEqual(rows[0.99]["frr"], 1.0)                    # ngưỡng quá cao: bỏ sót hết
        self.assertEqual(rows[0.99]["correct_rate"], 0.0)

    def test_stranger_accepted_when_threshold_below_their_similarity(self):
        stranger = noisy(self.people["p0"], 0.6, 7)                 # khá giống p0
        _, sim = best_match(self.gallery, stranger)
        rows = sweep_thresholds(self.gallery, [(None, stranger)], [sim - 0.01, sim + 0.01])
        self.assertEqual(rows[0]["far"], 1.0)
        self.assertEqual(rows[1]["far"], 0.0)

    def test_wrong_person_counted_separately(self):
        probe = noisy(self.people["p1"], 0.015, 3)
        rows = sweep_thresholds(self.gallery, [("p2", probe)], [0.5])    # nhãn thật sai cố ý
        self.assertEqual(rows[0]["wrong_person_rate"], 1.0)
        self.assertEqual(rows[0]["correct_rate"], 0.0)

    def test_pick_threshold_prefers_higher_on_tie(self):
        rows = sweep_thresholds(self.gallery, self.probes, frange(0.3, 0.6, 0.1))
        best = pick_threshold(rows)
        self.assertEqual(best["decision_acc"], 1.0)
        self.assertEqual(best["threshold"], 0.6)

    def test_frange(self):
        self.assertEqual(frange(0.3, 0.6, 0.1), [0.3, 0.4, 0.5, 0.6])
        self.assertEqual(len(frange(0.30, 0.60, 0.02)), 16)


class ReconcileTests(unittest.TestCase):
    def test_diff_ids(self):
        no_emb, no_cust = diff_ids([1, 2, 3, 4], [2, 3, 9])
        self.assertEqual(no_emb, {1, 4})
        self.assertEqual(no_cust, {9})
        self.assertEqual(diff_ids([1, 2], [1, 2]), (set(), set()))


try:
    import matplotlib  # noqa: F401
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


@unittest.skipUnless(HAS_MPL, "cần matplotlib")
class ChartSmokeTest(unittest.TestCase):
    def test_make_charts_runs_on_synthetic_results(self):
        """Dữ liệu tổng hợp trong thư mục tạm, chỉ để kiểm tra code vẽ biểu đồ chạy được."""
        from tools import make_charts
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                os.makedirs("results")
                for w, rps in ((1, 3.0), (2, 5.5), (3, 7.0)):
                    summary = {"label": f"w{w}", "workers": w, "requests": 30, "completed": 30, "failed": 0,
                               "wall_s": 30 / rps, "throughput_rps": rps, "concurrency": 6, "retried_requests": 0,
                               "latency": {"count": 30, "p50_ms": 1000 / w, "p95_ms": 1500 / w, "p99_ms": 1600 / w,
                                           "mean_ms": 1100 / w, "max_ms": 1700 / w},
                               "status_counts": {"RECOGNIZED": 30}, "per_worker": {"a": 15, "b": 15}}
                    json.dump({"summary": summary, "records": []}, open(f"results/bench_w{w}.json", "w"))
                rows = [{"threshold": 0.3 + 0.1 * i, "correct_rate": 0.9, "frr": 0.1, "wrong_person_rate": 0.0,
                         "far": 0.5 - 0.1 * i, "decision_acc": 0.9} for i in range(4)]
                json.dump({"rows": rows, "best": rows[2]}, open("results/threshold_eval.json", "w"))
                self.assertEqual(make_charts.main(), 0)
                for f in ("throughput_vs_workers.png", "latency_vs_workers.png", "threshold_sweep.png", "results_tables.md"):
                    self.assertTrue(os.path.getsize(os.path.join("docs", "figures", f)) > 0, f)
            finally:
                os.chdir(old)


class MergePlanTests(unittest.TestCase):
    """tools/merge_customers.plan: phần tính toán thuần tuý (SQL thật chưa chạy trên CSDL thật)."""

    def test_keep_name_and_fill_missing_phone(self):
        from datetime import datetime
        from tools.merge_customers import plan
        k = {"full_name": "Khang", "phone": None, "last_seen_at": datetime(2026, 11, 1)}
        d = {"full_name": None, "phone": "0900", "last_seen_at": datetime(2026, 11, 5)}
        r = plan(k, d, 2, 2, 5)
        self.assertEqual((r["full_name"], r["phone"]), ("Khang", "0900"))
        self.assertFalse(r["set_name"])
        self.assertTrue(r["set_phone"] and r["set_last_seen"])
        self.assertEqual(r["embeddings_after"], 4)
        self.assertEqual(r["warnings"], [])

    def test_warn_on_conflicting_names_and_cap(self):
        from tools.merge_customers import plan
        k = {"full_name": "A", "phone": None, "last_seen_at": None}
        d = {"full_name": "B", "phone": None, "last_seen_at": None}
        r = plan(k, d, 4, 3, 5)
        self.assertEqual(r["full_name"], "A")
        self.assertEqual(len(r["warnings"]), 2)
        self.assertFalse(r["set_last_seen"])


if __name__ == "__main__":
    unittest.main()


class FakeAmqpChannel:
    """Mô phỏng đúng hành vi gây lỗi cũ: message bị nack(requeue) quay về ĐẦU hàng đợi."""

    def __init__(self, bodies):
        self.queue = list(bodies)
        self.unacked = {}
        self._tag = 0

    def basic_get(self, queue, auto_ack=False):
        if not self.queue:
            return None, None, None
        body = self.queue.pop(0)
        self._tag += 1
        self.unacked[self._tag] = body
        return type("M", (), {"delivery_tag": self._tag})(), type("P", (), {"headers": None})(), body

    def basic_nack(self, delivery_tag, multiple=False, requeue=True):
        tags = sorted(t for t in self.unacked if (t <= delivery_tag if multiple else t == delivery_tag))
        back = [self.unacked.pop(t) for t in tags]
        if requeue:
            self.queue[0:0] = back


class PeekQueueTests(unittest.TestCase):
    def test_shows_each_message_once_and_loses_nothing(self):
        ch = FakeAmqpChannel([b"m1", b"m2", b"m3"])
        seen = peek_queue(ch, "face.requests.dlq", 10)
        self.assertEqual([body for _, body in seen], [b"m1", b"m2", b"m3"])      # trước đây in m1 ba lần
        self.assertEqual(ch.queue, [b"m1", b"m2", b"m3"])                           # trả lại đủ, đúng thứ tự
        self.assertEqual(ch.unacked, {})

    def test_limit_and_empty(self):
        ch = FakeAmqpChannel([b"a", b"b", b"c", b"d"])
        self.assertEqual(len(peek_queue(ch, "q", 2)), 2)
        self.assertEqual(ch.queue, [b"a", b"b", b"c", b"d"])
        self.assertEqual(peek_queue(FakeAmqpChannel([]), "q", 10), [])
