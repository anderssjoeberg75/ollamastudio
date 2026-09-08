"""Enhetstester för huggingface.py – bara standardbiblioteket, inget nätverk.

Allt som testas här är rena funktioner: namn-normalisering, tolkning av
API-svar, kvantiseringsval och rankning. Nätverksanropen (`search_models`,
`list_gguf_files`) rörs aldrig – de matas med sparade exempelsvar istället.

Kör: python3 -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import huggingface as hf  # noqa: E402


class TestNames(unittest.TestCase):
    def test_normalize_hf_urls(self):
        cases = {
            "https://huggingface.co/bartowski/Qwen3-8B-GGUF": "hf.co/bartowski/Qwen3-8B-GGUF",
            "http://www.huggingface.co/bartowski/Qwen3-8B-GGUF/": "hf.co/bartowski/Qwen3-8B-GGUF",
            "huggingface.co/bartowski/Qwen3-8B-GGUF/tree/main": "hf.co/bartowski/Qwen3-8B-GGUF",
            "hf.co/bartowski/Qwen3-8B-GGUF": "hf.co/bartowski/Qwen3-8B-GGUF",
            "  https://huggingface.co/a/b?library=true#files  ": "hf.co/a/b",
        }
        for raw, want in cases.items():
            self.assertEqual(hf.normalize_name(raw), want, raw)

    def test_normalize_keeps_ollama_names(self):
        for name in ("llama3.2:3b", "mistral", "qwen2.5:7b-instruct", ""):
            self.assertEqual(hf.normalize_name(name), name)

    def test_normalize_file_link_becomes_quant_tag(self):
        url = ("https://huggingface.co/bartowski/Qwen3-8B-GGUF/blob/main/"
               "Qwen3-8B-Q5_K_M.gguf")
        self.assertEqual(hf.normalize_name(url), "hf.co/bartowski/Qwen3-8B-GGUF:Q5_K_M")
        url = url.replace("/blob/", "/resolve/")
        self.assertEqual(hf.normalize_name(url), "hf.co/bartowski/Qwen3-8B-GGUF:Q5_K_M")

    def test_ref_helpers(self):
        self.assertEqual(hf.pull_ref("a/b"), "hf.co/a/b")
        self.assertEqual(hf.pull_ref("a/b", "Q4_K_M"), "hf.co/a/b:Q4_K_M")
        self.assertEqual(hf.split_ref("hf.co/a/b:Q4_K_M"), ("a/b", "Q4_K_M"))
        self.assertEqual(hf.split_ref("hf.co/a/b"), ("a/b", None))
        self.assertTrue(hf.is_hf_ref("hf.co/a/b"))
        self.assertTrue(hf.is_hf_ref("huggingface.co/a/b"))
        self.assertFalse(hf.is_hf_ref("llama3.2"))

    def test_search_terms(self):
        self.assertEqual(hf.search_terms("mistral-nemo:12b"), ("mistral-nemo", None))
        self.assertEqual(hf.search_terms("bartowski/Qwen3-8B"), ("Qwen3-8B", "bartowski"))
        self.assertEqual(hf.search_terms("hf.co/bartowski/Qwen3-8B:Q4_K_M"),
                         ("Qwen3-8B", "bartowski"))

    def test_quant_from_filename(self):
        cases = {
            "Qwen3-8B-Q4_K_M.gguf": "Q4_K_M",
            "model.IQ3_XXS.gguf": "IQ3_XXS",
            "Meta-Llama-3-8B.Q8_0.gguf": "Q8_0",
            "big-model-f16-00001-of-00003.gguf": "F16",
            "sub/dir/x-Q6_K.gguf": "Q6_K",
            "ggml-model.gguf": None,
        }
        for name, want in cases.items():
            self.assertEqual(hf.quant_from_filename(name), want, name)


class TestRepoIdValidation(unittest.TestCase):
    def test_accepts_normal_repos(self):
        for repo in ("bartowski/Qwen3-8B-GGUF", "a/b", "TheBloke/Mistral-7B-v0.1-GGUF",
                     "mradermacher/model_name.v2",
                     " /a/b/ "):                 # omgivande blanksteg/snedstreck städas bort
            self.assertTrue(hf.valid_repo_id(repo), repo)

    def test_rejects_paths_and_junk(self):
        # Skyddar mot att ett inskrivet "repo" pekar om API-anropet någon annanstans.
        for repo in ("", None, "utan-snedstreck", "a/b/c", "../../etc", "a/../b",
                     "a b/c", "https://evil.example/x", ".hidden/x"):
            self.assertFalse(hf.valid_repo_id(repo), repo)

    def test_list_gguf_files_refuses_invalid_repo(self):
        # Ska returnera tomt utan att ens försöka nå nätet.
        self.assertEqual(hf.list_gguf_files("../secret"), [])


class TestMissingModelError(unittest.TestCase):
    def test_missing_markers(self):
        for msg in ("pull model manifest: file does not exist",
                    'model "viking" not found',
                    "HTTP 404: {}",
                    "repository name must be lowercase"):
            self.assertTrue(hf.is_missing_model_error(msg), msg)

    def test_other_errors_are_not_missing(self):
        # Nätverks-/diskfel ska INTE trigga en Hugging Face-sökning.
        for msg in ("", None, "connection refused", "read timed out",
                    "write /root/.ollama: no space left on device"):
            self.assertFalse(hf.is_missing_model_error(msg), msg)


class TestParsing(unittest.TestCase):
    SEARCH = [
        {"id": "bartowski/Qwen3-8B-GGUF", "downloads": 51234, "likes": 88,
         "lastModified": "2026-01-02T00:00:00.000Z"},
        {"id": "meta/gated-model-GGUF", "downloads": 10, "likes": 1, "gated": "auto"},
        {"modelId": "someone/Old-Style-GGUF", "downloads": None},
        {"id": "trasig-utan-snedstreck"},          # hoppas över
        "inte ens en dict",                        # hoppas över
    ]

    def test_parse_search(self):
        out = hf.parse_search(self.SEARCH)
        self.assertEqual([m["id"] for m in out],
                         ["bartowski/Qwen3-8B-GGUF", "meta/gated-model-GGUF",
                          "someone/Old-Style-GGUF"])
        self.assertEqual(out[0]["owner"], "bartowski")
        self.assertEqual(out[0]["downloads"], 51234)
        self.assertEqual(out[0]["url"], "https://huggingface.co/bartowski/Qwen3-8B-GGUF")
        self.assertTrue(out[1]["gated"])
        self.assertFalse(out[0]["gated"])
        self.assertEqual(out[2]["downloads"], 0)   # None → 0, inte krasch

    def test_parse_search_accepts_dict_wrapper(self):
        self.assertEqual(len(hf.parse_search({"models": self.SEARCH})), 3)
        self.assertEqual(hf.parse_search(None), [])

    def test_parse_files_from_tree(self):
        tree = [
            {"type": "file", "path": "README.md", "size": 900},
            {"type": "directory", "path": "sub"},
            {"type": "file", "path": "Qwen3-8B-Q4_K_M.gguf", "size": 134,
             "lfs": {"size": 4_920_000_000}},
            {"type": "file", "path": "sub/Qwen3-8B-Q8_0.gguf", "size": 8_100_000_000},
        ]
        files = hf.parse_files(tree)
        self.assertEqual([f["file"] for f in files],
                         ["Qwen3-8B-Q4_K_M.gguf", "sub/Qwen3-8B-Q8_0.gguf"])
        self.assertEqual(files[0]["size"], 4_920_000_000)   # LFS-storleken, inte pekaren
        self.assertEqual(files[0]["quant"], "Q4_K_M")

    def test_parse_files_from_siblings(self):
        files = hf.parse_files({"siblings": [{"rfilename": "x-Q4_K_M.gguf"},
                                             {"rfilename": "config.json"}]})
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["size"], 0)               # siblings saknar storlek


class TestQuants(unittest.TestCase):
    FILES = [
        {"file": "m-Q4_K_M.gguf", "size": 4_000_000_000, "quant": "Q4_K_M"},
        {"file": "m-Q8_0-00001-of-00002.gguf", "size": 4_500_000_000, "quant": "Q8_0"},
        {"file": "m-Q8_0-00002-of-00002.gguf", "size": 4_500_000_000, "quant": "Q8_0"},
        {"file": "m.gguf", "size": 1_000, "quant": "okänd"},
    ]

    def test_group_quants_sums_shards(self):
        groups = {g["quant"]: g for g in hf.group_quants(self.FILES)}
        self.assertEqual(groups["Q8_0"]["parts"], 2)
        self.assertEqual(groups["Q8_0"]["size"], 9_000_000_000)
        self.assertEqual(groups["Q4_K_M"]["parts"], 1)
        self.assertIn("OKÄND", groups)

    def test_pick_quant_prefers_q4_k_m(self):
        self.assertEqual(hf.pick_quant(hf.group_quants(self.FILES))["quant"], "Q4_K_M")

    def test_pick_quant_falls_back_in_order(self):
        only_big = hf.group_quants([{"file": "a-Q8_0.gguf", "size": 8, "quant": "Q8_0"},
                                    {"file": "b-Q6_K.gguf", "size": 6, "quant": "Q6_K"}])
        self.assertEqual(hf.pick_quant(only_big)["quant"], "Q6_K")   # Q6_K före Q8_0
        self.assertIsNone(hf.pick_quant([]))

    def test_pick_quant_skips_unknown_when_possible(self):
        mixed = hf.group_quants([{"file": "a.gguf", "size": 1, "quant": "okänd"},
                                 {"file": "b-IQ4_XS.gguf", "size": 9, "quant": "IQ4_XS"}])
        self.assertEqual(hf.pick_quant(mixed)["quant"], "IQ4_XS")


class TestRanking(unittest.TestCase):
    MODELS = hf.parse_search([
        {"id": "bartowski/Qwen3-8B-GGUF", "downloads": 50_000, "likes": 40},
        {"id": "annan/qwen3-8b-abliterated-GGUF", "downloads": 900, "likes": 2},
        {"id": "gated-org/Qwen3-8B-GGUF", "downloads": 900_000, "likes": 900,
         "gated": True},
        {"id": "helt/annan-modell", "downloads": 9_000_000, "likes": 5_000},
    ])

    def test_exact_name_wins_over_downloads(self):
        ranked = hf.rank_candidates("qwen3-8b", self.MODELS)
        self.assertEqual(ranked[0]["id"], "bartowski/Qwen3-8B-GGUF")
        self.assertEqual(ranked[0]["match"], 1.0)

    def test_irrelevant_hits_are_filtered(self):
        ids = [m["id"] for m in hf.rank_candidates("qwen3-8b", self.MODELS)]
        self.assertNotIn("helt/annan-modell", ids)
        # …men med tröskeln avstängd (fritextsökningen i UI:t) visas de.
        ids = [m["id"] for m in hf.rank_candidates("qwen3-8b", self.MODELS,
                                                   min_similarity=0.0)]
        self.assertIn("helt/annan-modell", ids)

    def test_gated_is_penalised(self):
        ranked = hf.rank_candidates("qwen3-8b", self.MODELS)
        gated = next(m for m in ranked if m["id"].startswith("gated-org/"))
        self.assertGreater(ranked[0]["score"], gated["score"])

    def test_owner_in_query_gives_bonus(self):
        ranked = hf.rank_candidates("annan/qwen3-8b-abliterated", self.MODELS)
        self.assertEqual(ranked[0]["id"], "annan/qwen3-8b-abliterated-GGUF")

    def test_empty_input(self):
        self.assertEqual(hf.rank_candidates("qwen", []), [])
        self.assertEqual(hf.rank_candidates("", self.MODELS), [])


if __name__ == "__main__":
    unittest.main()
