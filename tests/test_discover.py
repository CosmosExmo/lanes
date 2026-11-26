import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

# `discover -s tests` does not import tests/__init__.py, so repeat the path setup
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lanelib import discover, state
from lanelib.discover import Pool
from lanelib.quota import Window

ADAPTERS = json.loads((ROOT / "adapters.json").read_text())
NOW = datetime(2026, 11, 26, 14, 0, tzinfo=ZoneInfo("Europe/Istanbul")).timestamp()
DAY = 86400


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.log = self.tmp / "stub.log"
        env = {"HOME": str(self.home), "LANES_CACHE": str(self.tmp / "cache"),
               "LANES_CONFIG": str(self.tmp / "config.json"), "STUB_LOG": str(self.log),
               "PATH": f"{ROOT / 'tests' / 'stubs'}{os.pathsep}{os.environ['PATH']}"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in ADAPTERS["claude"]["env_strip"] + ["STUB_MODE", "OPENAI_BASE_URL"]:
            os.environ.pop(k, None)
        os.environ["STUB_PI_READY"] = ""  # pi reaches no provider unless a test says so
        os.environ["STUB_OC"] = "deepseek:key"
        self.adapters = copy.deepcopy(ADAPTERS)

    def mode(self, m):
        os.environ["STUB_MODE"] = m

    def put(self, rel, data):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data if isinstance(data, str) else json.dumps(data))

    def calls(self):
        try:
            return [json.loads(l) for l in self.log.read_text().splitlines()]
        except OSError:
            return []

    def run_discover(self, probe=False, refresh=False, now=NOW, cfg=None):
        return discover.discover(self.adapters, cfg or state.load_config(), refresh, probe, now)


class SingleCheck(Base):
    def test_missing_bin_is_not_installed(self):
        self.adapters["claude"]["bin"] = "no-such-cli-xyz"
        (p,) = discover.check_claude(self.adapters, NOW)
        self.assertEqual((p.id, p.status), ("anthropic", "not_installed"))

    def test_missing_flag_is_adapter_broken(self):
        self.mode("nohelp")
        (p,) = discover.check_claude(self.adapters, NOW)
        self.assertEqual(p.status, "adapter_broken")
        self.assertIn("--model", p.note)

    def test_claude_subscription_with_usage(self):
        (p,) = discover.check_claude(self.adapters, NOW)
        self.assertEqual((p.status, p.billing, p.cli), ("usable", "subscription", "claude"))
        ws = {w.name: w for w in p.windows}
        self.assertEqual(ws["5h"].used_pct, 13.0)
        self.assertEqual(ws["weekly"].used_pct, 86.0)
        self.assertIn("opus", p.models)
        self.assertEqual(p.access, [{"cli": "claude", "provider": None, "models": p.models}])

    def test_exhausted_model_scoped_window_keeps_pool_usable(self):
        self.mode("fable_full")
        (p,) = discover.check_claude(self.adapters, NOW)
        self.assertEqual({w.name: w.used_pct for w in p.windows}["weekly:fable"], 100.0)
        self.assertEqual(p.status, "usable")

    def test_exhausted_pool_wide_window_is_exhausted(self):
        self.assertEqual(discover._quota_status([Window("weekly", 100, NOW + 3600, 10080)], NOW), "exhausted")

    def test_claude_console_auth_is_api_billing(self):
        self.mode("api")
        (p,) = discover.check_claude(self.adapters, NOW)
        self.assertEqual((p.status, p.billing), ("api_billing", "api"))

    def test_claude_logged_out(self):
        self.mode("logged_out")
        (p,) = discover.check_claude(self.adapters, NOW)
        self.assertEqual(p.status, "not_logged_in")

    def test_unparsable_usage_is_quota_unknown_not_zero(self):
        self.mode("usage_bad")
        (p,) = discover.check_claude(self.adapters, NOW)
        self.assertEqual((p.status, p.windows, p.note), ("quota_unknown", [], "quota parse failed"))

    def test_codex_api_auth_mode_is_api_billing(self):
        self.put(".codex/auth.json", {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-secret"})
        (p,) = discover.check_codex(self.adapters, NOW)
        self.assertEqual((p.id, p.status, p.billing), ("chatgpt", "api_billing", "api"))

    def test_codex_chatgpt_reads_rpc_quota_and_models(self):
        self.put(".codex/auth.json", {"auth_mode": "chatgpt", "tokens": {"access_token": "t"}})
        (p,) = discover.check_codex(self.adapters, NOW)
        self.assertEqual((p.status, p.billing, p.overage), ("usable", "subscription", True))
        self.assertEqual([(w.name, w.used_pct) for w in p.windows], [("weekly", 39.0)])
        self.assertEqual(p.models, ["gpt-5.6-luna", "gpt-6-sol"])  # hidden model dropped

    def test_codex_bad_rpc_shape_is_quota_unknown(self):
        self.put(".codex/auth.json", {"auth_mode": "chatgpt"})
        self.mode("rpc_bad")
        (p,) = discover.check_codex(self.adapters, NOW)
        self.assertEqual((p.status, p.windows, p.note), ("quota_unknown", [], "quota parse failed"))

    def test_codex_without_auth_file_is_not_logged_in(self):
        (p,) = discover.check_codex(self.adapters, NOW)
        self.assertEqual(p.status, "not_logged_in")

    def test_agy_pools_models_and_overage(self):
        self.mode("credits")
        self.put(".gemini/antigravity-cli/settings.json", {"useG1Credits": True})
        ps = {p.id: p for p in discover.check_agy(self.adapters, NOW)}
        self.assertEqual(set(ps), {"google:gemini", "google:claude-gpt"})
        self.assertEqual(ps["google:gemini"].status, "usable")
        self.assertTrue(ps["google:claude-gpt"].overage)
        self.assertEqual(ps["google:gemini"].models, ["gemini-3.8-flash-low", "gemini-4-pro-high"])
        self.assertEqual(ps["google:claude-gpt"].models, ["claude-sonnet-5-5-low", "gpt-oss-120b-medium"])
        self.assertEqual({w.name for w in ps["google:gemini"].windows}, {"5h", "weekly"})

    def test_agy_api_mode(self):
        self.mode("agy_api")
        ps = discover.check_agy(self.adapters, NOW)
        self.assertEqual([(p.id, p.status, p.billing) for p in ps], [("google:gemini", "api_billing", "api")])

    def test_agy_settings_model_provider_gemini_is_api_without_calling_usage(self):
        self.put(".gemini/antigravity-cli/settings.json", {"modelProvider": "gemini"})
        (p,) = discover.check_agy(self.adapters, NOW)
        self.assertEqual(p.status, "api_billing")
        self.assertFalse(any("/usage" in c for c in self.calls()))

    def test_cursor_quota_off_by_default(self):
        with mock.patch.object(discover, "_cursor_token") as tok, \
                mock.patch.object(discover, "_cursor_post") as post:
            (p,) = discover.check_cursor(self.adapters, state.load_config(), NOW)
        tok.assert_not_called()
        post.assert_not_called()
        self.assertEqual((p.status, p.windows, p.overage), ("logged_in_unverified", [], False))
        self.assertEqual(p.models, ["composer-2.5", "claude-sonnet-5-medium", "gpt-5.6-sol-high"])

    def test_cursor_quota_on_reads_usage_and_overage(self):
        fx = json.loads((ROOT / "tests/fixtures/cursor_usage.json").read_text())
        resp = {"GetCurrentPeriodUsage": fx, "GetHardLimit": {"noUsageBasedAllowed": False}}
        with mock.patch.object(discover, "_cursor_token", return_value="tok"), \
                mock.patch.object(discover, "_cursor_post", side_effect=lambda m, t: resp[m]):
            (p,) = discover.check_cursor(self.adapters, {"cursor_read_quota": True}, NOW)
        self.assertEqual((p.status, p.overage), ("usable", True))
        self.assertEqual(p.windows[0].used_pct, 1.0)

    def test_stripped_env_key_does_not_mark_api_billing(self):
        for k in ("CURSOR_API_KEY", "CURSOR_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            os.environ[k] = "key"
        (c,) = discover.check_cursor(self.adapters, state.load_config(), NOW)
        self.assertEqual((c.status, c.billing), ("logged_in_unverified", "subscription"))
        ps = discover.check_agy(self.adapters, NOW)
        self.assertEqual({(p.id, p.status, p.billing) for p in ps},
                         {("google:gemini", "usable", "subscription"), ("google:claude-gpt", "usable", "subscription")})

    def test_kimi_logged_in_is_unverified(self):
        (p,) = discover.check_kimi(self.adapters, NOW)
        self.assertEqual((p.status, p.models), ("logged_in_unverified", ["k3"]))

    def test_kimi_logged_out(self):
        self.mode("logged_out")
        (p,) = discover.check_kimi(self.adapters, NOW)
        self.assertEqual(p.status, "not_logged_in")


class Routers(Base):
    def pi_auth(self, **providers):
        self.put(".pi/agent/auth.json", {k: {"type": "oauth", "access": "SECRET"} for k in providers})

    def test_router_anthropic_never_routed(self):
        os.environ["STUB_PI_READY"] = "anthropic,xai"
        self.pi_auth(anthropic=1, xai=1)
        ids = [p.id for p in discover.check_router("pi", self.adapters, NOW)]
        self.assertEqual(ids, ["xai"])

    def test_pi_unknown_provider_has_unknown_billing(self):
        os.environ["STUB_PI_READY"] = "mystery"
        self.pi_auth(mystery=1)
        (p,) = discover.check_router("pi", self.adapters, NOW)
        self.assertEqual((p.id, p.billing, p.cli), ("unknown:mystery", "unknown", "pi"))
        self.assertEqual(p.access, [{"cli": "pi", "provider": "mystery", "models": ["model-a"]}])

    def test_pi_not_ready_provider_omitted(self):
        os.environ["STUB_PI_READY"] = "xai"
        self.pi_auth(xai=1, **{"kimi-coding": 1})
        self.assertEqual([p.id for p in discover.check_router("pi", self.adapters, NOW)], ["xai"])

    def test_pi_never_prints_bearer_token(self):
        os.environ["STUB_PI_READY"] = "xai"
        self.pi_auth(xai=1)
        discover.check_router("pi", self.adapters, NOW)
        self.assertFalse(any("print-bearer-token" in c for c in self.calls()))

    def test_opencode_oauth_openai_is_chatgpt_subscription(self):
        os.environ["STUB_OC"] = "openai:oauth"
        (p,) = discover.check_router("opencode", self.adapters, NOW)
        self.assertEqual((p.id, p.billing, p.status, p.cli), ("chatgpt", "subscription", "logged_in_unverified", "opencode"))
        self.assertEqual(p.access, [{"cli": "opencode", "provider": "openai", "models": ["model-a"]}])

    def test_opencode_api_key_provider_is_api_billing(self):
        os.environ["STUB_OC"] = "deepseek:key"
        (p,) = discover.check_router("opencode", self.adapters, NOW)
        self.assertEqual((p.id, p.billing, p.status), ("api:deepseek", "api", "api_billing"))

    def test_opencode_openai_key_only_is_api(self):
        os.environ["STUB_OC"] = "openai:key"
        (p,) = discover.check_router("opencode", self.adapters, NOW)
        self.assertEqual((p.id, p.billing), ("api:openai", "api"))

    def test_opencode_never_calls_credential_list(self):
        os.environ["STUB_OC"] = "openai:oauth,deepseek:key,anthropic:oauth"
        ps = discover.check_router("opencode", self.adapters, NOW)
        self.assertEqual(sorted(p.id for p in ps), ["api:deepseek", "chatgpt"])
        flat = " ".join(" ".join(c) for c in self.calls())
        for bad in ("credential.list", "export", "opencode.jsonc"):
            self.assertNotIn(bad, flat)

    def test_pi_credential_type_decides_billing(self):
        """C2: only an oauth credential is the subscription; an API key is pay-per-use."""
        os.environ["STUB_PI_READY"] = "openai-codex,xai,kimi-coding,anthropic,mystery"
        os.environ["STUB_PI_AUTH"] = "openai-codex:api_key,xai:oauth,kimi-coding:missing,anthropic:api_key,mystery:oauth"
        self.pi_auth(**{"openai-codex": 1, "xai": 1, "kimi-coding": 1, "anthropic": 1, "mystery": 1})
        got = {p.id: (p.billing, p.status) for p in discover.check_router("pi", self.adapters, NOW)}
        self.assertEqual(got, {"api:openai-codex": ("api", "api_billing"), "xai": ("subscription", "logged_in_unverified"),
                               "api:kimi-coding": ("api", "api_billing"),
                               "unknown:mystery": ("unknown", "logged_in_unverified")})  # anthropic never routed

    def test_opencode_oauth_plus_stored_key_is_unknown_billing(self):
        os.environ["STUB_OC"] = "openai:oauth+key,zai-coding-plan:oauth+key"
        got = {p.id: p.billing for p in discover.check_router("opencode", self.adapters, NOW)}
        self.assertEqual(got, {"unknown:openai": "unknown", "unknown:zai-coding-plan": "unknown"})

    def test_opencode_coding_plan_credential_is_subscription(self):
        os.environ["STUB_OC"] = "zai-coding-plan:key,kimi-for-coding:key"
        got = {p.id: p.billing for p in discover.check_router("opencode", self.adapters, NOW)}
        self.assertEqual(got, {"zai": "subscription", "kimi": "subscription"})

    def test_opencode_stripped_env_key_does_not_count_as_a_connection(self):
        os.environ["STUB_OC"] = "openai:oauth"
        os.environ["OPENAI_API_KEY"] = "sk-test"  # stripped from the child env, so opencode never sees it
        (p,) = discover.check_router("opencode", self.adapters, NOW)
        self.assertEqual((p.id, p.billing), ("chatgpt", "subscription"))

    def test_every_adapter_strips_every_known_provider_key(self):
        keys = {"OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                "CURSOR_API_KEY", "CURSOR_AUTH_TOKEN", "MOONSHOT_API_KEY", "KIMI_API_KEY", "XAI_API_KEY",
                "DEEPSEEK_API_KEY", "ZHIPU_API_KEY", "ZAI_API_KEY", "DASHSCOPE_API_KEY", "OPENROUTER_API_KEY",
                "GROQ_API_KEY", "MISTRAL_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN"}
        plan = {"BAILIAN_CODING_PLAN_API_KEY", "BAILIAN_TOKEN_PLAN_API_KEY"}
        for cli, a in ADAPTERS.items():
            self.assertLessEqual(keys, set(a["env_strip"]), cli)
            # qwen's coding-plan keys are subscription keys: only qwen keeps them
            self.assertEqual(plan <= set(a["env_strip"]), cli != "qwen", cli)
            self.assertEqual(plan & set(a["env_strip"]) == set(), cli == "qwen", cli)
        os.environ.update({k: "x" for k in keys | plan})
        env = discover.child_env(ADAPTERS["qwen"], "read")
        self.assertEqual(sorted(plan & set(env)), sorted(plan))
        self.assertFalse(keys & set(env))
        self.assertFalse(plan & set(discover.child_env(ADAPTERS["pi"], "read")))

    def test_qwen_coding_plan_base_url_is_subscription(self):
        self.put(".qwen/settings.json", {
            "security": {"auth": {"selectedType": "openai"}}, "model": {"name": "qwen3-coder-plus"},
            "modelProviders": {"openai": [{"id": "qwen3-coder-plus",
                                           "baseUrl": "https://coding-intl.dashscope.aliyuncs.com/v1"}]}})
        (p,) = discover.check_router("qwen", self.adapters, NOW)
        self.assertEqual((p.id, p.billing, p.status, p.models), ("qwen", "subscription", "logged_in_unverified", ["qwen3-coder-plus"]))

    def test_qwen_other_base_url_is_api(self):
        self.put(".qwen/settings.json", {"security": {"auth": {"selectedType": "openai"}},
                                         "modelProviders": {"openai": [{"id": "m", "baseUrl": "https://api.openai.com/v1"}]}})
        (p,) = discover.check_router("qwen", self.adapters, NOW)
        self.assertEqual((p.id, p.billing, p.status), ("api:qwen", "api", "api_billing"))

    def test_qwen_env_base_url_wins(self):
        self.put(".qwen/settings.json", {"security": {"auth": {"selectedType": "openai"}}})
        os.environ["OPENAI_BASE_URL"] = "https://coding.dashscope.aliyuncs.com/v1"
        (p,) = discover.check_router("qwen", self.adapters, NOW)
        self.assertEqual(p.billing, "subscription")

    def test_qwen_nothing_configured_is_not_logged_in(self):
        (p,) = discover.check_router("qwen", self.adapters, NOW)
        self.assertEqual((p.id, p.status), ("qwen", "not_logged_in"))

    def test_qwen_subscription_credentials(self):
        self.put(".qwen/subscription_credentials.json", {"tokens": {"access": "x"}})
        (p,) = discover.check_router("qwen", self.adapters, NOW)
        self.assertEqual((p.id, p.billing), ("qwen", "subscription"))
        self.put(".qwen/subscription_credentials.json", {})
        (p,) = discover.check_router("qwen", self.adapters, NOW)
        self.assertEqual(p.status, "not_logged_in")

    def test_router_copies_windows_from_official_pool(self):
        os.environ["STUB_OC"] = "openai:oauth"
        official = Pool("chatgpt", "codex", "usable", "subscription", False,
                        [Window("weekly", 10.0, NOW + 1000, 10080)], [], "", NOW)
        (p,) = discover.check_router("opencode", self.adapters, NOW, {"chatgpt": official})
        self.assertEqual([w.name for w in p.windows], ["weekly"])


class Discover(Base):
    def test_pi_openai_codex_shares_chatgpt_pool(self):
        os.environ["STUB_PI_READY"] = "openai-codex"
        self.put(".codex/auth.json", {"auth_mode": "chatgpt"})
        self.put(".pi/agent/auth.json", {"openai-codex": {"type": "oauth", "access": "SECRET"}})
        inv = self.run_discover()
        self.assertEqual(inv["version"], 1)
        chatgpt = inv["pools"]["chatgpt"]
        self.assertEqual([(a["cli"], a["provider"]) for a in chatgpt["access"]],
                         [("codex", None), ("pi", "openai-codex")])
        self.assertEqual(chatgpt["cli"], "codex")
        self.assertEqual(chatgpt["status"], "usable")
        self.assertEqual([w["name"] for w in chatgpt["windows"]], ["weekly"])
        self.assertEqual(sum(1 for k in inv["pools"] if k == "chatgpt"), 1)

    def test_router_takes_over_dead_official_pool(self):
        os.environ["STUB_PI_READY"] = "openai-codex"
        self.put(".pi/agent/auth.json", {"openai-codex": {"type": "oauth"}})
        inv = self.run_discover()  # no ~/.codex/auth.json: codex not logged in
        c = inv["pools"]["chatgpt"]
        self.assertEqual((c["cli"], c["status"]), ("pi", "logged_in_unverified"))
        self.assertEqual([a["cli"] for a in c["access"]], ["pi"])

    def test_inventory_has_no_email(self):
        os.environ["STUB_PI_READY"] = "openai-codex"
        self.put(".codex/auth.json", {"auth_mode": "chatgpt", "tokens": {"access_token": "SECRET"}})
        self.put(".pi/agent/auth.json", {"openai-codex": {"type": "oauth", "access": "SECRET"}})
        inv = self.run_discover()
        dumped = json.dumps(inv)
        self.assertNotIn("@", dumped)
        self.assertNotIn("SECRET", dumped)
        self.assertNotIn("Example Org", dumped)
        self.assertEqual(state.read_inventory(None)["pools"].keys(), inv["pools"].keys())
        self.assertNotIn("@", (state.CACHE_DIR() / "inventory.json").read_text())

    def seed_exhausted(self, until):
        self.run_discover()
        inv = state.read_inventory(None)
        inv["pools"]["kimi"].update(status="exhausted", exhausted_until=until)
        state.write_inventory(inv)

    def test_future_exhausted_until_survives_rediscovery(self):
        self.seed_exhausted(NOW + 1800)
        for refresh in (False, True):
            k = self.run_discover(refresh=refresh)["pools"]["kimi"]
            self.assertEqual((k["status"], k["exhausted_until"]), ("exhausted", NOW + 1800))

    def test_exhaustion_mark_landing_during_discovery_survives(self):
        """I5: discovery's write is a locked read-modify-write; a limit mark written meanwhile is kept."""
        self.put(".codex/auth.json", {"auth_mode": "chatgpt"})
        self.mode("ok")
        self.run_discover()
        real = discover.check_kimi

        def late_mark(*a, **k):  # a runner marks chatgpt exhausted while discovery is still checking
            inv = state.read_inventory(None)
            inv["pools"]["chatgpt"].update(status="exhausted", exhausted_until=NOW + 3600)
            state.write_inventory(inv)
            return real(*a, **k)

        with mock.patch.object(discover, "check_kimi", late_mark):
            inv = self.run_discover(refresh=True)
        self.assertEqual((inv["pools"]["chatgpt"]["status"], inv["pools"]["chatgpt"]["exhausted_until"]),
                         ("exhausted", NOW + 3600))
        self.assertEqual(state.read_inventory(None)["pools"]["chatgpt"]["status"], "exhausted")

    def test_past_exhausted_until_is_cleared(self):
        self.seed_exhausted(NOW - 5)
        k = self.run_discover()["pools"]["kimi"]
        self.assertEqual((k["status"], k["exhausted_until"]), ("logged_in_unverified", 0.0))

    def test_login_without_plan_is_unverified(self):
        inv = self.run_discover(probe=False)
        self.assertEqual(inv["pools"]["kimi"]["status"], "logged_in_unverified")
        self.assertFalse(any(c[0] == "kimi" and "-p" in c for c in self.calls()))

    def test_probe_ok_is_usable(self):
        self.mode("ok")
        inv = self.run_discover(probe=True)
        self.assertEqual(inv["pools"]["kimi"]["status"], "usable")
        probe = [c for c in self.calls() if c[0] == "kimi" and "-p" in c][0]
        self.assertEqual(probe[probe.index("-p") + 1], "Reply with exactly: OK")
        self.assertEqual(probe[probe.index("-m") + 1], "k3")

    def test_probe_plan_error_is_no_plan(self):
        self.mode("plan_error")
        inv = self.run_discover(probe=True)
        self.assertEqual(inv["pools"]["kimi"]["status"], "no_plan")

    def test_no_plan_not_reprobed_for_24h(self):
        self.mode("plan_error")
        self.run_discover(probe=True)
        self.assertEqual(len([c for c in self.calls() if c[0] == "kimi" and "-p" in c]), 1)
        self.mode("ok")
        inv = self.run_discover(probe=True, now=NOW + 3600)
        self.assertEqual(inv["pools"]["kimi"]["status"], "no_plan")
        self.assertEqual(len([c for c in self.calls() if c[0] == "kimi" and "-p" in c]), 1)
        inv = self.run_discover(probe=True, now=NOW + DAY + 10)
        self.assertEqual(inv["pools"]["kimi"]["status"], "usable")

    def test_unknown_billing_pool_is_never_probed(self):
        os.environ["STUB_PI_READY"] = "mystery"
        self.put(".pi/agent/auth.json", {"mystery": {"type": "api_key"}})
        self.mode("ok")
        inv = self.run_discover(probe=True)
        self.assertEqual(inv["pools"]["unknown:mystery"]["status"], "logged_in_unverified")
        self.assertFalse(any(c[0] == "pi" and "-p" in c for c in self.calls()))
        self.assertEqual(inv["pools"]["api:deepseek"]["status"], "api_billing")

    def test_subscription_router_replaces_api_billed_official_pool(self):
        self.put(".codex/auth.json", {"auth_mode": "apikey"})
        os.environ["STUB_PI_READY"] = "openai-codex"
        c = self.run_discover()["pools"]["chatgpt"]
        self.assertEqual((c["cli"], c["billing"], c["status"]), ("pi", "subscription", "logged_in_unverified"))
        self.assertEqual([a["cli"] for a in c["access"]], ["pi"])

    def test_context_window_error_is_not_limit(self):
        for cli, a in self.adapters.items():
            for text in ("Error: context window exceeded", "request quota of tokens per message exceeded",
                         "please upgrade your client", "429 too many open files"):
                self.assertFalse(discover.is_limit_error(a, text), (cli, text))
        self.assertTrue(discover.is_limit_error(self.adapters["codex"], "You've hit your USAGE LIMIT, try later"))
        self.assertTrue(discover.is_limit_error(self.adapters["agy"], "code: resource_exhausted"))
        self.assertTrue(discover.is_limit_error(self.adapters["opencode"], "CreditLimitExceeded"))

    def test_child_env_strips_keys_and_applies_mode_env(self):
        os.environ["CURSOR_API_KEY"] = "k"
        self.assertNotIn("CURSOR_API_KEY", discover.child_env(self.adapters["cursor"]))
        oc = self.adapters["opencode"]
        self.assertIn("subagent", discover.child_env(oc, "read")["OPENCODE_CONFIG_CONTENT"])
        self.assertIn("edit", discover.child_env(oc, "read")["OPENCODE_CONFIG_CONTENT"])
        self.assertNotIn("edit", discover.child_env(oc, "write")["OPENCODE_CONFIG_CONTENT"])

    def test_probe_generic_failure_stays_unverified(self):
        inv = self.run_discover(probe=True)
        self.assertEqual(inv["pools"]["kimi"]["status"], "logged_in_unverified")

    def test_probe_refuses_api_unknown_and_dead_pools(self):
        self.mode("ok")
        (k,) = discover.check_kimi(self.adapters, NOW)
        for billing, status in (("api", "api_billing"), ("unknown", "logged_in_unverified"),
                                ("subscription", "not_logged_in")):
            p = Pool(**{**k.to_dict(), "windows": [], "billing": billing, "status": status})
            self.assertFalse(discover.probe(self.adapters, p), (billing, status))
            self.assertEqual(discover._probe(self.adapters, p), (False, False))
        self.assertFalse(any("-p" in c for c in self.calls()))  # no model call reached any stub
        self.assertTrue(discover.probe(self.adapters, k))  # a subscription pool still probes

    def test_probe_model_pick_avoids_restricted_first_model(self):
        """Real shape: opencode lists `gpt-5.6-cyber` first (not usable on a plain plan); the probe must use the adapter pick."""
        seen = []
        ok = mock.Mock(returncode=0, stdout="OK", stderr="")
        oc = self.adapters["opencode"]
        mk = lambda models, provider="openai": Pool(
            id="chatgpt", cli="opencode", status="usable", billing="subscription", overage=True, windows=[], note="", checked_at=NOW,
            access=[{"cli": "opencode", "provider": provider, "models": models}], models=models)
        with mock.patch.object(discover, "run_cmd", side_effect=lambda argv, *a, **k: seen.append(argv) or ok):
            self.assertTrue(discover.probe(self.adapters, mk(["gpt-5.6-cyber", "gpt-5.6-luna"])))
            self.assertTrue(discover.probe(self.adapters, mk(["gpt-5.6-cyber", "gpt-6-sol"])))  # pick gone: first listed
            self.assertTrue(discover.probe(self.adapters, mk(["glm-5.3"], "zai-coding-plan")))  # no pick for this provider
        self.assertEqual([a[a.index("-m") + 1] for a in seen],
                         ["openai/gpt-5.6-luna#low", "openai/gpt-5.6-cyber#low", "zai-coding-plan/glm-5.3#low"])

    def test_probe_function_returns_bool(self):
        self.mode("ok")
        (p,) = discover.check_kimi(self.adapters, NOW)
        self.assertTrue(discover.probe(self.adapters, p))
        self.mode("")
        self.assertFalse(discover.probe(self.adapters, p))

    def test_quota_reused_within_15_minutes_unless_refresh(self):
        self.run_discover()
        usage = lambda: len([c for c in self.calls() if c[0] == "claude" and "/usage" in c])
        self.assertEqual(usage(), 1)
        inv = self.run_discover(now=NOW + 600)
        self.assertEqual(usage(), 1)
        self.assertEqual({w["name"] for w in inv["pools"]["anthropic"]["windows"]}, {"5h", "weekly", "weekly:fable"})
        self.run_discover(now=NOW + 1000)
        self.assertEqual(usage(), 2)
        self.run_discover(now=NOW + 1100, refresh=True)
        self.assertEqual(usage(), 3)

    def test_model_lists_reused_within_a_day(self):
        self.put(".codex/auth.json", {"auth_mode": "chatgpt"})
        listed = lambda: len([c for c in self.calls() if c[0] == "codex" and c[1:3] == ["debug", "models"]])
        self.run_discover()
        self.run_discover(now=NOW + 1000)
        self.assertEqual(listed(), 1)
        self.run_discover(now=NOW + DAY + 10)
        self.assertEqual(listed(), 2)

    def test_failed_quota_read_is_not_reused_as_data(self):
        self.mode("usage_bad")
        inv = self.run_discover()
        self.assertEqual(inv["pools"]["anthropic"]["status"], "quota_unknown")
        self.mode("")
        inv = self.run_discover(now=NOW + 60)
        self.assertEqual(inv["pools"]["anthropic"]["status"], "usable")

    def test_not_installed_pools_are_listed(self):
        for k in self.adapters:
            self.adapters[k]["bin"] = "no-such-cli-xyz"
        inv = self.run_discover()
        self.assertEqual(inv["pools"]["anthropic"]["status"], "not_installed")
        self.assertEqual(inv["pools"]["kimi"]["status"], "not_installed")
        self.assertNotIn("chatgpt", {k for k, v in inv["pools"].items() if v["status"] == "usable"})

    def test_crashing_check_becomes_adapter_broken(self):
        with mock.patch.object(discover, "check_kimi", side_effect=RuntimeError("boom")):
            inv = self.run_discover()
        self.assertEqual(inv["pools"]["kimi"]["status"], "adapter_broken")


class Misc(Base):
    def test_pool_roundtrip(self):
        p = Pool("x", "c", "usable", "subscription", False, [Window("5h", 1.0, 2.0, 300)], ["m"], "n", 3.0,
                 [{"cli": "c", "provider": None, "models": ["m"]}])
        self.assertEqual(Pool.from_dict(json.loads(json.dumps(p.to_dict()))), p)
        self.assertEqual(Pool("x", "c", "usable", "subscription", False, [], [], "", 1.0).access, [])

    def test_run_cmd_never_raises(self):
        r = discover.run_cmd(["sh", "-c", "echo out; exit 3"], 5)
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "out"))
        self.assertEqual(discover.run_cmd(["sleep", "5"], 0.2).returncode, 124)
        self.assertNotEqual(discover.run_cmd(["no-such-cli-xyz"], 5).returncode, 0)

    def test_run_cmd_stdin_and_env(self):
        f = self.tmp / "in.txt"
        f.write_text("hello")
        r = discover.run_cmd(["sh", "-c", "cat; echo $FOO"], 5, env={"FOO": "bar", "PATH": os.environ["PATH"]},
                             stdin_path=str(f))
        self.assertEqual(r.stdout, "hellobar\n")
        r = discover.run_cmd(["cat"], 5)  # default stdin is /dev/null
        self.assertEqual(r.stdout, "")

    def test_fill_template_single_pass(self):
        out = discover.fill_template(["x", "{model}", "{prompt}"], model="m", prompt="a {model} b")
        self.assertEqual(out, ["x", "m", "a {model} b"])

    def test_detect_self_pool(self):
        self.assertEqual(discover.detect_self_pool({"CLAUDECODE": "1"}), "anthropic")
        self.assertIsNone(discover.detect_self_pool({}))
        # markers observed in the real tool shells of each harness
        for var, pool in (("CODEX_THREAD_ID", "chatgpt"), ("ANTIGRAVITY_AGENT", "google:gemini"),
                          ("CURSOR_AGENT", "cursor")):
            self.assertEqual(discover.detect_self_pool({var: "x"}), pool)
            self.assertEqual(discover.detect_self_pool({var: "x", "CLAUDECODE": "1"}), pool)  # launched from Claude
        pi = {"PI_CODING_AGENT": "true", "PI_PROVIDER": "openai-codex", "CLAUDECODE": "1"}
        self.assertEqual(discover.detect_self_pool(pi, ADAPTERS), "chatgpt")
        self.assertEqual(discover.detect_self_pool({**pi, "PI_PROVIDER": "xai"}, ADAPTERS), "xai")
        # pi never maps to an unrouted or api-billed pool
        self.assertIsNone(discover.detect_self_pool({**pi, "PI_PROVIDER": "anthropic"}, ADAPTERS))
        self.assertIsNone(discover.detect_self_pool({**pi, "PI_PROVIDER": "openai"}, ADAPTERS))
        # the innermost harness wins: pi under a Codex shell (inherited CODEX_THREAD_ID)
        self.assertEqual(discover.detect_self_pool({**pi, "CODEX_THREAD_ID": "t", "PI_PROVIDER": "xai"}, ADAPTERS), "xai")
        self.assertIsNone(discover.detect_self_pool({**pi, "CODEX_THREAD_ID": "t", "PI_PROVIDER": "anthropic"}, ADAPTERS))
        self.assertIsNone(discover.detect_self_pool({**pi, "PI_PROVIDER": "unlisted"}, ADAPTERS))
        # opencode's session id alone does not name a pool; the user's own OPENCODE_* settings are not a marker
        self.assertIsNone(discover.detect_self_pool({"OPENCODE_SESSION_ID": "s", "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1"}))
        self.assertIsNone(discover.detect_self_pool({"CODEX_COMPANION_SESSION_ID": "s"}))

    def test_agy_marker_follows_the_selected_model(self):
        """I1: the agy session pool depends on the model chosen in settings.json."""
        env = {"ANTIGRAVITY_AGENT": "1"}
        self.assertEqual(discover.detect_self_pool(env), "google:gemini")  # no settings file
        for model, pool in (("gemini-3.8-flash-low", "google:gemini"), ("claude-sonnet-5-5-low", "google:claude-gpt"),
                            ("gpt-oss-120b-medium", "google:claude-gpt"), ("", "google:gemini")):
            self.put(".gemini/antigravity-cli/settings.json", {"model": model})
            self.assertEqual(discover.detect_self_pool(env), pool, model)
        self.put(".gemini/antigravity-cli/settings.json", "not json")
        self.assertEqual(discover.detect_self_pool(env), "google:gemini")

    def test_read_pool_windows(self):
        ws = discover.read_pool_windows("anthropic", self.adapters, {}, NOW)
        self.assertEqual({w.name for w in ws}, {"5h", "weekly", "weekly:fable"})
        self.assertEqual({w.name for w in discover.read_pool_windows("google:gemini", self.adapters, {}, NOW)}, {"5h", "weekly"})
        self.assertIsNone(discover.read_pool_windows("kimi", self.adapters, {}, NOW))
        self.assertIsNone(discover.read_pool_windows("cursor", self.adapters, {"cursor_read_quota": False}, NOW))
        self.mode("usage_bad")
        self.assertIsNone(discover.read_pool_windows("anthropic", self.adapters, {}, NOW))

    def test_adapters_json_schema(self):
        need = {"bin", "verified_version", "version_cmd", "help_cmd", "required_flags", "quota", "effort_style",
                "run", "stdin", "env_strip", "limit_error_patterns", "can_shell", "probe_model", "verified"}
        self.assertEqual(set(ADAPTERS), {"claude", "codex", "agy", "cursor", "kimi", "pi", "opencode", "qwen"})
        for k, a in ADAPTERS.items():
            self.assertTrue(need <= set(a), k)
            self.assertIn("read", a["run"])
            self.assertIn("write", a["run"])
        self.assertFalse(ADAPTERS["kimi"]["verified"])
        self.assertIn("write_shell", ADAPTERS["claude"]["run"])
        for k in ("pi", "opencode", "qwen"):
            self.assertIn("providers", ADAPTERS[k])
            self.assertIn("nesting_guard", ADAPTERS[k])
        self.assertFalse(ADAPTERS["pi"]["providers"]["anthropic"]["route"])
        self.assertEqual(ADAPTERS["claude"]["nesting_guard"], ["--disallowedTools", "Agent"])

    def test_capabilities_json(self):
        caps = json.loads((ROOT / "capabilities.json").read_text())["models"]
        for m in caps:
            self.assertEqual(set(m), {"pattern", "pool", "cli", "tiers", "strengths"})
            self.assertTrue(set(m["tiers"]) <= {"T0", "T1", "T2"})
            self.assertTrue(set(m["strengths"]) <= {"code", "review", "research", "plan", "docs", "ui"})
        self.assertEqual({m["pool"] for m in caps if m["pattern"].startswith("claude-")},
                         {"google:claude-gpt", "cursor"})
        self.assertEqual([m["pattern"] for m in caps if m["pool"] == "anthropic"], ["haiku", "sonnet", "opus", "fable"])

    def test_stubs_exist_for_all_eight_clis(self):
        for n in ("claude", "codex", "agy", "cursor-agent", "kimi", "pi", "opencode", "qwen"):
            self.assertTrue(os.access(ROOT / "tests" / "stubs" / n, os.X_OK), n)


if __name__ == "__main__":
    unittest.main()
