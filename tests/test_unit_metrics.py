import unittest
import httpx
import nodus


class UnitMetricsTests(unittest.TestCase):
    def test_workload_exposes_server_measurements_without_deriving_cost(self):
        def handler(request):
            return httpx.Response(200, json={
                'id': 'wl_units', 'status': 'running', 'spend_usd': 999,
                'unit_metrics': {'units_completed': 3, 'p50_ms': 20, 'p95_ms': 29,
                                 'cost_per_unit_usd': 0.2, 'dropped_observations': 0}})
        with nodus.Client(api_key='test', base_url='https://test.invalid') as client:
            client._http.close()
            client._http = httpx.Client(base_url='https://test.invalid', transport=httpx.MockTransport(handler))
            work = client.get('wl_units')
            self.assertEqual(work.unit_metrics.units_completed, 3)
            self.assertEqual(work.unit_metrics.p50_ms, 20)
            self.assertEqual(work.unit_metrics.p95_ms, 29)
            self.assertEqual(work.unit_metrics.cost_per_unit_usd, 0.2)

    def test_missing_or_nonfinite_measurements_do_not_become_zero(self):
        for value in (None, {}, {'p50_ms': True, 'p95_ms': float('inf'),
                                  'cost_per_unit_usd': -1}):
            with self.subTest(value=value):
                metrics = nodus.UnitMetrics.from_dict(value)
                if metrics is not None:
                    self.assertIsNone(metrics.units_completed)
                    self.assertIsNone(metrics.p50_ms)
                    self.assertIsNone(metrics.p95_ms)
                    self.assertIsNone(metrics.cost_per_unit_usd)

    def test_unrepresentable_measurement_is_absent(self):
        self.assertIsNone(nodus.UnitMetrics.from_dict({'p50_ms': 10 ** 1000}).p50_ms)
