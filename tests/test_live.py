import json
import logging
import unittest

from pumpfun_lab.live import capture_market


MINT_A = "1" * 31 + "2"
MINT_B = "1" * 31 + "3"
MINT_C = "1" * 31 + "4"
SIGNATURE = "1" * 63 + "2"
SOL_MINT = "So11111111111111111111111111111111111111112"


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Socket:
    """Controlled network boundary: queued packets consume real logical time."""

    def __init__(self, clock, packets):
        self.clock = clock
        self.packets = list(packets)
        self.sent = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def send(self, message):
        self.sent.append(json.loads(message))

    def recv(self, *, timeout):
        if not self.packets:
            self.clock.advance(timeout)
            raise TimeoutError("no packet")
        delay, payload = self.packets[0]
        if delay > timeout:
            self.packets[0] = (delay - timeout, payload)
            self.clock.advance(timeout)
            raise TimeoutError("no packet yet")
        self.packets.pop(0)
        self.clock.advance(delay)
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload) if isinstance(payload, dict) else payload


class Connection:
    def __init__(self, socket):
        self.socket = socket
        self.calls = []

    def __call__(self, url, **options):
        self.calls.append((url, options))
        return self.socket


class LiveTests(unittest.TestCase):
    def capture(self, packets=(), **options):
        clock = Clock()
        socket = Socket(clock, packets)
        connection = Connection(socket)
        report = capture_market(
            connect_factory=connection, clock=clock,
            wall_clock=lambda: 1700000000.0,
            **{"duration_seconds": 3, **options},
        )
        return report, connection, socket

    def test_discovery_sends_only_free_methods_and_never_a_supplied_key(self):
        secret = "sentinel-secret/do-not-keep"
        report, connection, socket = self.capture([
            (0.1, {"txType": "create", "mint": MINT_A,
                   "uri": "https://invalid/" + secret, "name": secret,
                   "traderPublicKey": secret}),
            (0.1, {"txType": "buy", "mint": MINT_A,
                   "vSolInBondingCurve": 2, "vTokensInBondingCurve": 4}),
        ], api_key=secret)
        self.assertEqual([x[0] for x in connection.calls], ["wss://pumpportal.fun/api/data"])
        self.assertEqual(socket.sent, [{"method": "subscribeNewToken"},
                                       {"method": "subscribeMigration"}])
        self.assertEqual(report["observations"], [])
        self.assertEqual(report["subscribed_mints"], [])
        self.assertTrue(report["market_data_received"])
        self.assertNotIn(secret, json.dumps(report))
        self.assertNotIn("traderPublicKey", json.dumps(report))
        self.assertTrue(socket.closed)

    def test_metered_mode_requires_both_key_and_explicit_charge_consent(self):
        for settings in ({"api_key": "abc"}, {"accept_data_charges": True},
                         {"api_key": "  ", "accept_data_charges": True},
                         {"api_key": "abc", "accept_data_charges": 1}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.capture(mode="trades", **settings)

    def test_trade_mode_subscribes_each_new_mint_once_and_honors_token_cap(self):
        report, connection, socket = self.capture([
            (0.1, {"txType": "create", "mint": MINT_A}),
            (0.1, {"txType": "create", "mint": MINT_A}),
            (0.1, {"txType": "create", "mint": MINT_B}),
            (0.1, {"txType": "create", "mint": MINT_C}),
        ], mode="trades", max_tokens=2, api_key="a/b?c=2", accept_data_charges=True)
        self.assertEqual(len(connection.calls), 1)
        self.assertEqual(connection.calls[0][0], "wss://pumpportal.fun/api/data?api-key=a%2Fb%3Fc%3D2")
        self.assertEqual(socket.sent[2:], [
            {"method": "subscribeTokenTrade", "keys": [MINT_A]},
            {"method": "subscribeTokenTrade", "keys": [MINT_B]},
        ])
        self.assertEqual(report["subscribed_mints"], [MINT_A, MINT_B])
        self.assertEqual(report["counts"]["duplicates"], 1)
        self.assertNotIn("a/b?c=2", json.dumps(report))

    def test_trade_reserves_yield_indicative_price_at_receive_time_without_features(self):
        report, _, _ = self.capture([
            (0.1, {"txType": "create", "mint": MINT_A}),
            (0.9, {"txType": "buy", "mint": MINT_A, "signature": SIGNATURE,
                   "vSolInBondingCurve": 10, "vTokensInBondingCurve": 200,
                   "quoteMint": SOL_MINT, "timestamp": 1,
                   "marketCapSol": 1234, "traderPublicKey": MINT_B}),
        ], mode="trades", api_key="test-key", accept_data_charges=True)
        observation = report["observations"][0]
        self.assertEqual(observation["ts_ms"], 1700000001000)
        self.assertEqual(observation["mint"], MINT_A)
        self.assertAlmostEqual(observation["price_sol"], 0.05)
        for field in ("liquidity_sol", "sellable", "progress", "creator_prior_launches"):
            self.assertNotIn(field, observation)
        self.assertEqual(report["counts"]["buy"], 1)
        self.assertFalse(report["completeness"]["history_complete"])
        self.assertFalse(report["completeness"]["chain_verified"])

    def test_unsubscribed_trade_never_enters_replay(self):
        report, _, _ = self.capture([
            (0.1, {"txType": "buy", "mint": MINT_B,
                   "vSolInBondingCurve": 1, "vTokensInBondingCurve": 2}),
        ], mode="trades", api_key="test-key", accept_data_charges=True)
        self.assertEqual(report["observations"], [])
        self.assertEqual(report["counts"]["unsubscribed_trade"], 1)

    def test_non_sol_quotes_invalid_reserves_and_missing_fields_are_not_prices(self):
        invalid_fields = [
            {"quoteMint": MINT_B, "vSolInBondingCurve": 2, "vTokensInBondingCurve": 4},
            {"quoteCurrency": "USDC", "vSolInBondingCurve": 2, "vTokensInBondingCurve": 4},
            {"vSolInBondingCurve": 0, "vTokensInBondingCurve": 4},
            {"vSolInBondingCurve": True, "vTokensInBondingCurve": 4},
            {"vSolInBondingCurve": 1e308, "vTokensInBondingCurve": 1e-308},
            {"solAmount": 2, "tokenAmount": 4},
        ]
        packets = [(0.1, {"txType": "create", "mint": MINT_A})]
        packets.extend((0.1, {"txType": "sell", "mint": MINT_A, **fields})
                       for fields in invalid_fields)
        report, _, _ = self.capture(packets, mode="trades", api_key="test-key",
                                     accept_data_charges=True)
        self.assertEqual(report["observations"], [])
        self.assertEqual(report["counts"]["unsupported_price"], 6)

    def test_duplicate_trade_signature_does_not_repeat_observation(self):
        trade = {"txType": "buy", "mint": MINT_A, "signature": SIGNATURE,
                 "vSolInBondingCurve": 2, "vTokensInBondingCurve": 4}
        report, _, _ = self.capture([
            (0.1, {"txType": "create", "mint": MINT_A}),
            (0.1, trade), (0.1, trade),
        ], mode="trades", api_key="test-key", accept_data_charges=True)
        self.assertEqual(len(report["observations"]), 1)
        self.assertEqual(report["counts"]["duplicates"], 1)

    def test_handshake_and_acknowledgement_alone_are_not_market_evidence(self):
        report, _, _ = self.capture([(0.1, {"message": "Successfully subscribed to new token creation events."})])
        self.assertTrue(report["connected"])
        self.assertFalse(report["market_data_received"])
        self.assertEqual(report["counts"]["acknowledgements"], 1)
        self.assertEqual(report["observations"], [])
        self.assertEqual(report["end_reason"], "duration_limit")

    def test_duration_deadline_stops_waiting_without_processing_late_packets(self):
        report, _, socket = self.capture([(4, {"txType": "create", "mint": MINT_A})], duration_seconds=2)
        self.assertEqual(report["duration_seconds"], 2)
        self.assertEqual(report["counts"]["messages"], 0)
        self.assertFalse(report["market_data_received"])
        self.assertTrue(socket.closed)

    def test_message_limit_includes_invalid_messages(self):
        report, _, socket = self.capture([(0.1, "bad-json"),
                                         (0.1, {"txType": "create", "mint": MINT_A})], max_messages=1)
        self.assertEqual(report["counts"]["messages"], 1)
        self.assertEqual(report["counts"]["invalid"], 1)
        self.assertEqual(report["events"], [])
        self.assertEqual(report["end_reason"], "message_limit")
        self.assertEqual(len(socket.packets), 1)

    def test_provider_error_retains_partial_capture_without_echoing_secret(self):
        secret = "sensitive-secret"
        report, _, socket = self.capture([
            (0.1, {"txType": "create", "mint": MINT_A}),
            (0.1, {"errors": ["bad API key " + secret]}),
        ], mode="trades", api_key=secret, accept_data_charges=True)
        self.assertEqual(report["end_reason"], "provider_error")
        self.assertTrue(report["errors"])
        self.assertEqual(report["counts"]["new_token"], 1)
        self.assertNotIn(secret, json.dumps(report))
        self.assertTrue(socket.closed)

    def test_transport_exception_is_sanitized_and_does_not_reconnect(self):
        report, connection, socket = self.capture([
            (0.1, {"txType": "create", "mint": MINT_A}),
            (0.1, OSError("wss://pumpportal.fun/api/data?api-key=hidden-secret")),
        ])
        self.assertEqual(report["end_reason"], "connection_error")
        self.assertEqual(len(connection.calls), 1)
        self.assertNotIn("hidden-secret", json.dumps(report))
        self.assertEqual(len(report["events"]), 1)
        self.assertTrue(socket.closed)

    def test_connection_failure_returns_evidence_without_inventing_data(self):
        def fail(*_, **__):
            raise PermissionError("credential-in-exception")
        report = capture_market(connect_factory=fail, duration_seconds=1)
        self.assertFalse(report["connected"])
        self.assertFalse(report["market_data_received"])
        self.assertEqual(report["events"], [])
        self.assertEqual(report["observations"], [])
        self.assertEqual(report["end_reason"], "connection_error")
        self.assertNotIn("credential-in-exception", json.dumps(report))

    def test_malformed_nonfinite_oversized_and_invalid_mints_are_not_events(self):
        packets = [(0.1, "[1,2]"), (0.1, '{"x":NaN}'),
                   (0.1, b"\xff"), (0.1, "x" * 65537),
                   (0.1, {"txType": "create", "mint": "not-an-address"})]
        report, _, _ = self.capture(packets)
        self.assertEqual(report["counts"]["invalid"], 5)
        self.assertEqual(report["events"], [])

    def test_malformed_event_type_is_skipped_without_breaking_the_connection(self):
        report, _, _ = self.capture([
            (0.1, {"txType": [], "mint": MINT_A}),
            (0.1, {"txType": "create", "mint": MINT_B}),
        ])
        self.assertEqual(report["end_reason"], "duration_limit")
        self.assertEqual(report["counts"]["invalid"], 1)
        self.assertEqual(report["counts"]["new_token"], 1)
        self.assertEqual(report["errors"], [])

    def test_overflowed_json_number_invalidates_packet_before_event_count(self):
        packet = '{"txType":"create","mint":"' + MINT_A + '","vSolInBondingCurve":1e999}'
        report, _, _ = self.capture([(0.1, packet)])
        self.assertEqual(report["counts"]["invalid"], 1)
        self.assertFalse(report["market_data_received"])
        self.assertEqual(report["events"], [])

    def test_secret_cannot_be_reflected_in_a_structurally_valid_mint(self):
        report, _, _ = self.capture([(0.1, {"txType": "create", "mint": MINT_A})], api_key=MINT_A)
        self.assertNotIn(MINT_A, json.dumps(report))
        self.assertEqual(report["events"], [])

    def test_connection_uses_non_propagating_silent_logger(self):
        _, connection, _ = self.capture()
        logger = connection.calls[0][1]["logger"]
        self.assertIsInstance(logger, logging.Logger)
        self.assertFalse(logger.propagate)
        self.assertTrue(all(isinstance(handler, logging.NullHandler) for handler in logger.handlers))

    def test_options_are_bounded_before_any_connection(self):
        for options in ({"duration_seconds": 0}, {"duration_seconds": 901},
                        {"duration_seconds": True}, {"duration_seconds": 1.5},
                        {"max_messages": 0}, {"max_messages": 10001},
                        {"max_messages": False}, {"max_tokens": 0},
                        {"max_tokens": 21}, {"max_tokens": True},
                        {"mode": "real-trading"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.capture(**options)


if __name__ == "__main__":
    unittest.main()
