"""QLD 종가 비교: 기간 경계와 누락 자료를 네트워크 없이 검증."""
import unittest
import pandas as pd
from fetch_data import close_return, track_vs_bench


def frame(dates, prices):
    return pd.DataFrame({'Close': prices}, index=pd.to_datetime(dates))


class BenchmarkTests(unittest.TestCase):
    def test_actual_compound_return_and_future_exclusion(self):
        result = close_return(frame(['2026-08-17', '2026-08-18', '2026-08-19', '2026-08-20'],
                                    [100, 120, 96, 200]), '2026-08-17', '2026-08-19')
        self.assertEqual(result['pct'], -4)
        self.assertEqual(result['end'], '2026-08-19')

    def test_weekend_uses_previous_close(self):
        result = close_return(frame(['2026-08-14', '2026-08-21'], [100, 110]),
                              '2026-08-16', '2026-08-23')
        self.assertEqual((result['start'], result['end'], result['pct']),
                         ('2026-08-14', '2026-08-21', 10))

    def test_missing_start_or_stale_end_is_unavailable(self):
        self.assertIsNone(close_return(frame(['2026-08-18', '2026-08-19'], [100, 110]),
                                      '2026-08-17', '2026-08-19'))
        self.assertIsNone(close_return(frame(['2026-08-17', '2026-08-18'], [100, 110]),
                                      '2026-08-17', '2026-09-01'))
        self.assertIsNone(close_return(frame(['2026-08-17', '2026-08-18'], [0, float('nan')]),
                                      '2026-08-17', '2026-08-18'))

    def test_benchmark_does_not_join_ranked_holdings(self):
        hist = {f'2026-08-{day}': {**{f'S{i}': {'rank': i, 'px': 100} for i in range(1, 6)},
                                  'QQQ': {'px': 100}, 'QLD': {'px': 150}}
                for day in [17, 18, 19]}
        result = track_vs_bench(hist)
        self.assertEqual(result['holding'], ['S1', 'S2', 'S3', 'S4', 'S5'])
        self.assertEqual((result['strategy_pct'], result['bench_pct']), (0, 0))


if __name__ == '__main__':
    unittest.main()
