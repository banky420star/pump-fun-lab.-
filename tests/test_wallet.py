"""Wallet accounting tests use synthetic fixtures, never live wallet evidence."""

import importlib
import json
import os
import unittest
from copy import deepcopy
from unittest.mock import patch


WALLET = "1" * 31 + "2"  # Artificial 32-byte public key; not a user's wallet.
OTHER = "11111111111111111111111111111111"
MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL = "So11111111111111111111111111111111111111112"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


def token(index, amount, *, owner=WALLET, mint=MINT, decimals=6):
    record = {"accountIndex": index, "mint": mint,
              "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
              "uiTokenAmount": {"amount": str(amount), "decimals": decimals,
                                "uiAmount": None, "uiAmountString": "unused"}}
    if owner is not None:
        record["owner"] = owner
    return record


def transaction():
    return {"slot": 42, "blockTime": 1700000000, "version": "legacy",
            "transaction": {"signatures": ["synthetic-signature"], "message": {
                "accountKeys": [WALLET, OTHER, MINT, PUMP, SWAP],
                "header": {"numRequiredSignatures": 1, "numReadonlySignedAccounts": 0,
                           "numReadonlyUnsignedAccounts": 3},
                "recentBlockhash": "synthetic-blockhash", "instructions": []}},
            "meta": {"err": None, "fee": 5000, "preBalances": [1000000000, 0, 0, 0, 0],
                     "postBalances": [999995000, 0, 0, 0, 0],
                     "preTokenBalances": [], "postTokenBalances": [],
                     "innerInstructions": [], "logMessages": [], "rewards": [],
                     "loadedAddresses": {"writable": [], "readonly": []}}}


class WalletAddressTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("pumpfun_lab.wallet")
        except ModuleNotFoundError:
            self.fail("The wallet module has not been implemented")

    def test_address_decodes_to_32_bytes_including_leading_zeroes(self):
        wallet = self.module()
        self.assertEqual(wallet.validate_address("1" * 32), bytes(32))
        self.assertEqual(len(wallet.validate_address(WALLET)), 32)

    def test_rejects_non_base58_and_wrong_decoded_length(self):
        wallet = self.module()
        for address in ("", "1" * 31, "1" * 33, "0" * 32, "O" * 32,
                        " " + WALLET, WALLET + "\n", None, 123):
            with self.subTest(address=address), self.assertRaises(ValueError):
                wallet.validate_address(address)


class RpcClientTests(unittest.TestCase):
    def module(self):
        module = importlib.import_module("pumpfun_lab.wallet")
        self.assertTrue(hasattr(module, "RpcClient"), "Read-only RPC client is not implemented")
        return module

    def test_blocks_all_non_allowlisted_methods_before_transport(self):
        wallet = self.module()
        requests = []
        def transport(url, payload, timeout):
            requests.append(payload)
            return {"jsonrpc": "2.0", "id": payload["id"], "result": []}
        client = wallet.RpcClient("https://rpc.example.test", transport=transport)
        for method in ("sendTransaction", "requestAirdrop", "simulateTransaction", "getBalance"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                client.call(method, [])
        self.assertEqual(requests, [])
        self.assertEqual(client.call("getSignaturesForAddress", [WALLET]), [])
        self.assertEqual(len(requests), 1)

    def test_enforces_call_budget_and_transmits_timeout(self):
        wallet = self.module()
        requests = []
        def transport(url, payload, timeout):
            requests.append((payload, timeout))
            return {"jsonrpc": "2.0", "id": payload["id"], "result": {"slot": 7}}
        client = wallet.RpcClient("https://rpc.example.test", max_calls=2,
                                  timeout=3.5, transport=transport)
        self.assertEqual(client.call("getTransaction", ["synthetic1"]), {"slot": 7})
        self.assertEqual(client.call("getTransaction", ["synthetic2"]), {"slot": 7})
        with self.assertRaises(wallet.RpcError) as caught:
            client.call("getTransaction", ["synthetic3"])
        self.assertEqual(caught.exception.category, "call_budget_exhausted")
        self.assertEqual([timeout for _, timeout in requests], [3.5, 3.5])
        self.assertEqual([payload["id"] for payload, _ in requests], [1, 2])

    def test_rejects_unbounded_network_configuration(self):
        wallet = self.module()
        for timeout in (0, -1, 31, float("nan"), float("inf"), True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                wallet.RpcClient(timeout=timeout)
        for budget in (0, -1, 22, True, 2.5):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                wallet.RpcClient(max_calls=budget)

    def test_transport_errors_never_expose_credentials_or_url_paths(self):
        wallet = self.module()
        secret_url = "https://user:password@rpc.example.test/key-SECRET?api-key=QUERYSECRET"
        def transport(url, payload, timeout):
            raise RuntimeError("Connection failed for " + secret_url)
        client = wallet.RpcClient(secret_url, transport=transport)
        with self.assertRaises(wallet.RpcError) as caught:
            client.call("getSignaturesForAddress", [WALLET])
        serialized = json.dumps(client.records) + str(caught.exception) + client.endpoint_origin
        for secret in ("password", "key-SECRET", "QUERYSECRET", "user:"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(client.endpoint_origin, "https://rpc.example.test")

    def test_rejects_response_for_wrong_request_and_sanitizes_rpc_errors(self):
        wallet = self.module()
        def wrong_id(url, payload, timeout):
            return {"jsonrpc": "2.0", "id": 999, "result": []}
        client = wallet.RpcClient(transport=wrong_id)
        with self.assertRaises(wallet.RpcError) as caught:
            client.call("getSignaturesForAddress", [WALLET])
        self.assertEqual(caught.exception.category, "invalid_response")
        def rpc_error(url, payload, timeout):
            return {"jsonrpc": "2.0", "id": payload["id"],
                    "error": {"code": -32005, "message": "secret=DO_NOT_STORE"}}
        client = wallet.RpcClient(transport=rpc_error)
        with self.assertRaises(wallet.RpcError) as caught:
            client.call("getTransaction", ["synthetic"])
        self.assertEqual(caught.exception.code, -32005)
        self.assertNotIn("DO_NOT_STORE", str(caught.exception) + json.dumps(client.records))

    def test_blank_optional_rpc_environment_uses_public_default(self):
        wallet = self.module()
        for blank in ("", "   "):
            with self.subTest(blank=blank), patch.dict(os.environ, {"SOLANA_RPC_URL": blank}):
                try:
                    client = wallet.RpcClient()
                except ValueError:
                    self.fail("An unset optional CI secret must fall back to the public RPC endpoint")
                self.assertEqual(client.endpoint_origin, "https://api.mainnet.solana.com")


class TransactionNormalizationTests(unittest.TestCase):
    def normalize(self, tx, **kwargs):
        module = importlib.import_module("pumpfun_lab.wallet")
        self.assertTrue(hasattr(module, "normalize_transaction"), "Transaction parser is not implemented")
        return module.normalize_transaction(tx, WALLET, **kwargs)

    def test_native_delta_uses_wallet_index_and_does_not_subtract_fee_twice(self):
        tx = transaction()
        tx["transaction"]["message"]["accountKeys"][0:2] = [OTHER, WALLET]
        tx["meta"]["preBalances"] = [10000, 1000000000, 0, 0, 0]
        tx["meta"]["postBalances"] = [5000, 900000000, 0, 0, 0]
        row = self.normalize(tx)
        self.assertEqual(row["wallet_sol_delta_lamports"], -100000000)
        self.assertEqual(row["fee_lamports"], 5000)
        self.assertEqual(row["wallet_fee_lamports"], 0)
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["activity_classification"], "unclassified")

    def test_failed_transaction_retains_actual_fee_and_negative_sol_delta(self):
        tx = transaction()
        tx["meta"]["err"] = {"InstructionError": [1, {"Custom": 6000}]}
        row = self.normalize(tx)
        self.assertEqual(row["status"], "failure")
        self.assertEqual(row["fee_lamports"], 5000)
        self.assertEqual(row["wallet_fee_lamports"], 5000)
        self.assertEqual(row["wallet_sol_delta_lamports"], -5000)

    def test_resolves_loaded_addresses_without_duplicating_json_parsed_keys(self):
        tx = transaction()
        tx["version"] = 0
        tx["transaction"]["message"]["accountKeys"] = [OTHER]
        tx["meta"]["loadedAddresses"] = {"writable": [WALLET], "readonly": []}
        tx["meta"]["preBalances"] = [5000, 100]
        tx["meta"]["postBalances"] = [0, 223]
        self.assertEqual(self.normalize(tx)["wallet_sol_delta_lamports"], 123)
        tx["transaction"]["message"]["accountKeys"] = [
            {"pubkey": OTHER, "signer": True, "writable": True, "source": "transaction"},
            {"pubkey": WALLET, "signer": False, "writable": True, "source": "lookupTable"}]
        self.assertEqual(self.normalize(tx)["wallet_sol_delta_lamports"], 123)

    def test_aggregates_same_mint_wallet_accounts_using_exact_raw_integers(self):
        tx = transaction()
        tx["meta"]["preTokenBalances"] = [token(1, "1000000000000000000"), token(2, 1000000),
                                                token(3, 50, owner=OTHER)]
        tx["meta"]["postTokenBalances"] = [token(1, "1000000000000000001"), token(2, 3000000),
                                                 token(3, 100, owner=OTHER)]
        row = self.normalize(tx)
        self.assertEqual(len(row["token_deltas"]), 1)
        delta = row["token_deltas"][0]
        self.assertEqual((delta["mint"], delta["owner"]), (MINT, WALLET))
        self.assertEqual(delta["pre_amount_raw"], "1000000000001000000")
        self.assertEqual(delta["post_amount_raw"], "1000000000003000001")
        self.assertEqual(delta["delta_raw"], "2000001")
        self.assertEqual(delta["delta_token_amount"], "2.000001")

    def test_wrapped_sol_stays_a_separate_token_delta(self):
        tx = transaction()
        tx["meta"]["preTokenBalances"] = [token(1, 1000000000, mint=WSOL, decimals=9)]
        tx["meta"]["postTokenBalances"] = [token(1, 0, mint=WSOL, decimals=9)]
        row = self.normalize(tx)
        self.assertEqual(row["wallet_sol_delta_lamports"], -5000)
        self.assertEqual(row["token_deltas"][0]["delta_token_amount"], "-1")
        self.assertTrue(row["token_deltas"][0]["is_wrapped_sol"])

    def test_unknown_ownership_is_unresolved_instead_of_assumed_wallet_property(self):
        tx = transaction()
        tx["meta"]["preTokenBalances"] = [token(1, 100, owner=None)]
        tx["meta"]["postTokenBalances"] = [token(1, 200)]
        row = self.normalize(tx)
        self.assertEqual(row["token_deltas"], [])
        self.assertIn("unknown_token_owner", row["data_quality_flags"])
        self.assertEqual(len(row["unresolved_token_balances"]), 2)

    def test_missing_balance_array_does_not_mean_zero_pre_balance(self):
        tx = transaction()
        del tx["meta"]["preTokenBalances"]
        tx["meta"]["postTokenBalances"] = [token(1, 100)]
        row = self.normalize(tx)
        self.assertEqual(row["token_deltas"], [])
        self.assertIn("token_balance_data_unavailable", row["data_quality_flags"])

    def test_account_creation_and_closure_use_zero_only_when_both_arrays_exist(self):
        for pre, post, expected in (([], [token(1, 100)], "100"),
                                    ([token(1, 100)], [], "-100")):
            with self.subTest(delta=expected):
                tx = transaction()
                tx["meta"]["preTokenBalances"] = pre
                tx["meta"]["postTokenBalances"] = post
                self.assertEqual(self.normalize(tx)["token_deltas"][0]["delta_raw"], expected)

    def test_inconsistent_decimals_cannot_produce_a_false_token_total(self):
        tx = transaction()
        tx["meta"]["preTokenBalances"] = [token(1, 100, decimals=6)]
        tx["meta"]["postTokenBalances"] = [token(1, 100, decimals=9)]
        row = self.normalize(tx)
        self.assertEqual(row["token_deltas"], [])
        self.assertIn("token_decimals_mismatch", row["data_quality_flags"])

    def test_pump_reference_requires_an_instruction_and_never_labels_a_trade(self):
        tx = transaction()
        self.assertEqual(self.normalize(tx)["pump_program_instruction_references"], [])
        tx["transaction"]["message"]["instructions"] = [
            {"programId": SWAP, "accounts": [WALLET], "data": "synthetic"}]
        tx["meta"]["innerInstructions"] = [{"index": 0, "instructions": [
            {"programIdIndex": 3, "accounts": [0, 1], "data": "synthetic", "stackHeight": 2}]}]
        row = self.normalize(tx)
        observed = {item["program_id"]: item["evidence"]
                    for item in row["pump_program_instruction_references"]}
        self.assertEqual(observed, {PUMP: ["inner_instruction"], SWAP: ["outer_instruction"]})
        self.assertEqual(row["activity_classification"], "unclassified")

    def test_missing_transaction_and_metadata_are_flagged_not_zero_filled(self):
        row = self.normalize(None, signature="synthetic-missing",
                             signature_info={"slot": 7, "blockTime": None, "err": None})
        self.assertFalse(row["transaction_available"])
        self.assertEqual(row["signature"], "synthetic-missing")
        self.assertIsNone(row["wallet_sol_delta_lamports"])
        self.assertIsNone(row["fee_lamports"])
        self.assertIn("transaction_unavailable", row["data_quality_flags"])
        tx = transaction()
        tx["meta"] = None
        row = self.normalize(tx)
        self.assertIsNone(row["wallet_sol_delta_lamports"])
        self.assertIn("transaction_metadata_unavailable", row["data_quality_flags"])

    def test_malformed_balances_are_reported_without_invented_amounts(self):
        tx = transaction()
        tx["meta"]["postBalances"][0] = "999995000"
        tx["meta"]["postTokenBalances"] = [token(1, "not-an-integer")]
        row = self.normalize(tx)
        self.assertIsNone(row["wallet_sol_delta_lamports"])
        self.assertEqual(row["token_deltas"], [])
        self.assertIn("wallet_sol_balance_unavailable", row["data_quality_flags"])
        self.assertIn("malformed_token_balance", row["data_quality_flags"])


class WalletHistoryTests(unittest.TestCase):
    def module(self):
        module = importlib.import_module("pumpfun_lab.wallet")
        self.assertTrue(hasattr(module, "fetch_wallet_history"), "History ingestion is not implemented")
        return module

    def test_bounds_sample_preserves_raw_and_counts_missing_separately_from_unfetched(self):
        wallet = self.module()
        signatures = [{"signature": "synthetic-a", "slot": 42, "err": None,
                       "memo": None, "blockTime": 1700000000, "confirmationStatus": "finalized"},
                      {"signature": "synthetic-b", "slot": 41, "err": None,
                       "memo": None, "blockTime": None, "confirmationStatus": "finalized"},
                      {"signature": "synthetic-c", "slot": 40, "err": None,
                       "memo": None, "blockTime": 1699999998, "confirmationStatus": "finalized"}]
        tx = transaction()
        tx["transaction"]["signatures"] = ["synthetic-a"]
        calls = []
        def transport(url, payload, timeout):
            calls.append(deepcopy(payload))
            if payload["method"] == "getSignaturesForAddress":
                result = signatures
            else:
                result = tx if payload["params"][0] == "synthetic-a" else None
            return {"jsonrpc": "2.0", "id": payload["id"], "result": deepcopy(result)}
        report = wallet.fetch_wallet_history(WALLET, "https://rpc.example.test", 3, 2, transport=transport)
        self.assertEqual(report["raw"]["signatures"], signatures)
        self.assertEqual(report["raw"]["transactions"][0]["result"], tx)
        self.assertIsNone(report["raw"]["transactions"][1]["result"])
        self.assertEqual(len(report["rows"]), 2)
        coverage = report["completeness"]
        self.assertEqual(coverage["signatures_observed"], 3)
        self.assertEqual(coverage["transactions_requested"], 2)
        self.assertEqual(coverage["transactions_fetched"], 1)
        self.assertEqual(coverage["missing_transaction_count"], 1)
        self.assertEqual(coverage["unfetched_signature_count"], 1)
        self.assertTrue(coverage["lookback_limited"])
        self.assertFalse(coverage["history_complete"])
        self.assertFalse(coverage["pnl_ready"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]["params"], [WALLET, {"commitment": "finalized", "limit": 3}])
        self.assertEqual(calls[1]["params"][1], {"commitment": "finalized", "encoding": "json",
                                                "maxSupportedTransactionVersion": 0})

    def test_default_limits_cannot_generate_more_than_21_read_requests(self):
        wallet = self.module()
        requests = []
        def transport(url, payload, timeout):
            requests.append(payload)
            result = [{"signature": "synthetic-" + str(i), "err": None, "slot": 1000-i,
                       "blockTime": 1700000000-i, "memo": None, "confirmationStatus": "finalized"}
                      for i in range(100)] if payload["method"] == "getSignaturesForAddress" else None
            return {"jsonrpc": "2.0", "id": payload["id"], "result": result}
        report = wallet.fetch_wallet_history(WALLET, transport=transport)
        self.assertEqual(len(requests), 21)
        self.assertEqual(report["completeness"]["missing_transaction_count"], 20)
        self.assertEqual(report["completeness"]["unfetched_signature_count"], 80)

    def test_invalid_address_or_limits_are_rejected_before_network(self):
        wallet = self.module()
        calls = []
        def transport(url, payload, timeout):
            calls.append(payload)
            raise AssertionError("Invalid configuration must not reach the network")
        for sig_limit, tx_limit in ((0, 0), (101, 20), (100, 21), (100, -1), (True, 1), (1, 1.5)):
            with self.subTest(limits=(sig_limit, tx_limit)), self.assertRaises(ValueError):
                wallet.fetch_wallet_history(WALLET, max_signatures=sig_limit, max_transactions=tx_limit,
                                            transport=transport)
        with self.assertRaises(ValueError):
            wallet.fetch_wallet_history("not-a-public-key", transport=transport)
        self.assertEqual(calls, [])

    def test_network_failure_returns_provenance_without_fabricating_wallet_rows(self):
        wallet = self.module()
        def transport(url, payload, timeout):
            raise TimeoutError("https://rpc.example.test/API-SECRET")
        report = wallet.fetch_wallet_history(WALLET, transport=transport)
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["raw"], {"signatures": [], "transactions": []})
        self.assertFalse(report["completeness"]["signature_query_succeeded"])
        self.assertEqual(report["provenance"]["calls_made"], 1)
        self.assertEqual(report["errors"][0]["category"], "timeout")
        self.assertNotIn("API-SECRET", json.dumps(report))

    def test_duplicate_signatures_are_not_fetched_twice_and_bad_records_are_flagged(self):
        wallet = self.module()
        requests = []
        def transport(url, payload, timeout):
            requests.append(payload)
            result = [{"signature": "synthetic-a", "err": None},
                      {"signature": "synthetic-a", "err": None}, {}]
            return {"jsonrpc": "2.0", "id": payload["id"],
                    "result": result if payload["method"] == "getSignaturesForAddress" else None}
        report = wallet.fetch_wallet_history(WALLET, max_signatures=3, transport=transport)
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(report["raw"]["signatures"]), 3)
        self.assertEqual(report["completeness"]["signatures_observed"], 1)
        self.assertIn("duplicate_signatures", report["completeness"]["data_quality_flags"])
        self.assertIn("malformed_signature_records", report["completeness"]["data_quality_flags"])

    def test_environment_url_is_used_but_never_copied_to_report(self):
        wallet = self.module()
        observed = []
        secret_url = "https://rpc.example.test/KEY-SECRET?key=QUERY-SECRET"
        def transport(url, payload, timeout):
            observed.append(url)
            return {"jsonrpc": "2.0", "id": payload["id"], "result": []}
        with patch.dict(os.environ, {"SOLANA_RPC_URL": secret_url}):
            report = wallet.fetch_wallet_history(WALLET, max_transactions=0, transport=transport)
        self.assertEqual(observed, [secret_url])
        self.assertEqual(report["provenance"]["rpc_origin"], "https://rpc.example.test")
        self.assertNotIn("SECRET", json.dumps(report))
        self.assertFalse(report["completeness"]["history_complete"])


if __name__ == "__main__":
    unittest.main()
