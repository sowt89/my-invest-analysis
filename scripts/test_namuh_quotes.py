"""인증정보 보호, API 오류, 가격/배수 일관성을 네트워크 없이 검증."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import pandas as pd

from namuh_quotes import NamuhClient, NamuhError, parse_quote
from fetch_data import sec_multiple, fetch_stock


def response(data, status=200):
    return Mock(status_code=status, json=Mock(return_value=data), text=json.dumps(data))


def token():
    return response({"access_token": "private-token-example", "expires_in": 86400})


def quote(price="71,200", code="005930"):
    return {"Output_0": {"iem_cd": code, "stck_prpr": price,
                         "prdy_ctrt": "-1.25", "acml_vol": "123,456"}}


class NamuhTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch("namuh_quotes.time.sleep").start()
        self.addCleanup(patch.stopall)

    def test_quote_allowlist_and_numeric_validation(self):
        data = quote()
        data["account"] = "must-not-publish"
        parsed = parse_quote(data, "005930")
        self.assertEqual((parsed["price"], parsed["volume"], parsed["day_change_pct"]),
                         (71200, 123456, -1.25))
        self.assertNotIn("account", parsed)
        for bad in ["0", "-10", "NaN", "Infinity", ""]:
            with self.assertRaises(NamuhError):
                parse_quote(quote(bad), "005930")

    def test_empty_wrong_symbol_and_business_error(self):
        for data in [{}, {"rsp_cd": "error"}, {"Output_0": []}, quote(code="000660")]:
            with self.assertRaises(NamuhError):
                parse_quote(data, "005930")

    def test_request_shape_and_etf_endpoint(self):
        session = Mock()
        session.post.side_effect = [token(), response(quote()), response(quote(code="069500"))]
        client = NamuhClient("test-key", "test-secret", session=session)
        client.quote("005930.KS")
        client.quote("069500.KS", is_etf=True)
        calls = session.post.call_args_list
        self.assertEqual(calls[0].kwargs["params"]["appsecretkey"], "test-secret")
        self.assertEqual(calls[1].kwargs["json"], {"Input_0": {"iem_cd": "005930", "market_cd": "KRX"}})
        self.assertTrue(calls[2].args[0].endswith("/etfCurrent"))
        self.assertEqual(calls[2].kwargs["json"], {"Input_0": {"iem_cd": "069500"}})
        self.assertFalse(calls[1].kwargs["allow_redirects"])

    def test_invalid_token_refreshes_once(self):
        session = Mock()
        session.post.side_effect = [token(), response({}, 401), token(), response(quote())]
        self.assertEqual(NamuhClient("k", "s", session=session).quote("005930.KS")["price"], 71200)
        self.assertEqual(session.post.call_count, 4)

    def test_rate_limit_does_not_refresh_token(self):
        session = Mock()
        session.post.side_effect = [token(), response({}, 429)]
        with self.assertRaisesRegex(NamuhError, "429"):
            NamuhClient("k", "s", session=session).quote("005930.KS")
        self.assertEqual(session.post.call_count, 2)

    def test_network_error_never_contains_credentials(self):
        session = Mock()
        session.post.side_effect = requests.ConnectionError("https://host?appsecretkey=secret")
        with self.assertRaises(NamuhError) as error:
            NamuhClient("key", "secret", session=session).quote("005930.KS")
        self.assertNotIn("secret", str(error.exception))

    def test_encrypted_cache_reused_across_clients(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token.enc"
            session = Mock()
            session.post.side_effect = [token(), response(quote())]
            NamuhClient("key", "secret", path, session).quote("005930.KS")
            self.assertNotIn(b"private-token-example", path.read_bytes())
            next_session = Mock()
            next_session.post.return_value = response(quote())
            NamuhClient("key", "secret", path, next_session).quote("005930.KS")
            self.assertEqual(next_session.post.call_count, 1)
            wrong = NamuhClient("key", "changed-secret", path, Mock())
            self.assertIsNone(wrong.token)

    def test_latest_price_changes_current_multiple_not_history(self):
        rows = [{"sh": 10, "niTtm": 100, "filed": "2020-01-01"}] * 9
        dates, closes = ["2026-01-01"] * 24, [100] * 24
        self.assertEqual(sec_multiple(rows, dates, closes, "niTtm"), (10, 10))
        self.assertEqual(sec_multiple(rows, dates, closes, "niTtm", current_price=120), (10, 12))
        self.assertEqual(sec_multiple(rows[:-1] + [{**rows[-1], "niTtm": -100}],
                                      dates, closes, "niTtm", current_price=120), (10, None))

    def test_quote_flows_into_momentum_and_valuation(self):
        stock = Mock()
        stock.info = {"currentPrice": 100, "marketCap": 1e10,
                      "forwardPE": 10, "priceToSalesTrailing12Months": 2}
        history = pd.DataFrame({"Close": [100.] * 60, "Volume": [1000.] * 60},
                               index=pd.date_range("2025-01-01", periods=60, freq="W"))
        stock.history.return_value = history
        for field in ("quarterly_income_stmt", "income_stmt", "cashflow", "revenue_estimate",
                      "earnings_estimate", "earnings_history"):
            setattr(stock, field, pd.DataFrame())
        stock.calendar = {}
        with patch("fetch_data.yf.Ticker", return_value=stock):
            result = fetch_stock(None, "005930.KS", "삼성전자", "반도체", {},
                                 parse_quote(quote("120"), "005930"))
        self.assertEqual(result["price"], 120)
        self.assertEqual(result["detail"]["ma200Dist"], 20)
        self.assertEqual(result["mom_score"], 10)
        self.assertEqual(result["detail"]["fwdPer"], 12)
        self.assertEqual(result["detail"]["psr"], 2.4)
        self.assertEqual(result["market_cap_b"], 12)

    def test_failed_quote_preserves_old_timestamp_and_skips_history(self):
        import fetch_data as collector
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data_kr.json"
            old_stock = {"ticker": "005930.KS", "name": "삼성전자", "theme": "반도체",
                         "price": 70000, "above_ma200": True, "mom_score": 10,
                         "quote": {"source": "NAMUH PLUG", "fetched_at": "2026-10-30T06:30:00Z"}}
            path.write_text(json.dumps({"updated_at": "2026-10-30T06:31:00Z", "stocks": [old_stock]}))
            cfg = {**collector.MARKETS["kr"], "watchlist": [("005930.KS", "삼성전자", "반도체")]}
            with patch.dict("os.environ", {"NAMUH_APP_KEY": "k", "NAMUH_APP_SECRET": "s"}), \
                    patch.object(collector, "_ROOT", directory), \
                    patch.dict(collector.MARKETS, {"kr": cfg}), \
                    patch("sys.argv", ["fetch_data.py", "kr"]), \
                    patch.object(collector, "make_session"), \
                    patch.object(collector, "fetch_market", return_value={"spy_dd_52w": 0, "spy_ma200_above": True}), \
                    patch.object(collector, "load_history", return_value={}), \
                    patch.object(collector, "load_pit", return_value={}), \
                    patch.object(collector, "save_history") as save, \
                    patch.object(collector.NamuhClient, "from_env", side_effect=NamuhError("인증 실패")), \
                    patch("builtins.print"), patch.object(collector.traceback, "print_exc"):
                collector.main()
            result = json.loads(path.read_text())
            self.assertEqual(result["stocks"][0]["price"], 70000)
            self.assertEqual(result["stocks"][0]["quote"], old_stock["quote"])
            self.assertEqual(result["stocks"][0]["quote_status"], "stale")
            self.assertEqual(result["namuh"]["status"], "failed")
            save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
