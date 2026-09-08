"""Enhetstester för soup_train.py – bara standardbiblioteket, inga processer.

Allt som testas här är rena funktioner: konfigbygge, validering, granskning av
träningsdata och tolkning av loggrader. Soup självt anropas aldrig.

Kör: python3 -m unittest discover -s tests
"""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import soup_train as st  # noqa: E402


class TestNames(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(st.safe_name("Min Svenska Modell 2!"), "min-svenska-modell-2")
        self.assertEqual(st.safe_name("  ../hack/../x  "), "hack-x")
        self.assertEqual(st.safe_name(""), "min-modell")
        self.assertEqual(st.safe_name("!!!", "reserv"), "reserv")
        self.assertLessEqual(len(st.safe_name("a" * 200)), 48)

    def test_ollama_name_is_prefixed(self):
        self.assertEqual(st.ollama_model_name("Kundtjänst Bot"), "soup-kundtj-nst-bot")


class TestProfiles(unittest.TestCase):
    def test_suggest_from_vram(self):
        self.assertEqual(st.suggest_profile(0), "cpu")
        self.assertEqual(st.suggest_profile(4096), "4gb")
        self.assertEqual(st.suggest_profile(8188), "8gb")
        self.assertEqual(st.suggest_profile(16376), "16gb")
        self.assertEqual(st.suggest_profile(24564), "24gb")

    def test_profile_fills_blanks_but_user_wins(self):
        filled = st.apply_profile({"profile": "4gb"})
        self.assertEqual(filled["quantization"], "4bit")
        self.assertTrue(filled["stream_layers"])
        self.assertEqual(filled["max_length"], 1024)
        kept = st.apply_profile({"profile": "4gb", "max_length": 4096, "quantization": "none"})
        self.assertEqual(kept["max_length"], 4096)      # egna val skrivs inte över
        self.assertEqual(kept["quantization"], "none")

    def test_unknown_profile_falls_back(self):
        self.assertEqual(st.profile("finns-inte")["id"], "4gb")


class TestBuildYaml(unittest.TestCase):
    FORM = {"name": "Kundtjänst Bot", "base": "Qwen/Qwen2.5-1.5B-Instruct", "task": "sft",
            "data": "data/mitt.jsonl", "format": "auto", "profile": "8gb",
            "epochs": 3, "lr": "2e-5", "val_split": 0.1}

    def test_core_keys(self):
        y = st.build_yaml(self.FORM)
        self.assertIn("base: Qwen/Qwen2.5-1.5B-Instruct", y)
        self.assertIn("task: sft", y)
        self.assertIn("train: ./data/mitt.jsonl", y)   # relativa vägar får ./
        self.assertIn("epochs: 3", y)
        self.assertIn("quantization: 4bit", y)         # från profilen
        self.assertIn("output: ./runs/kundtj-nst-bot", y)
        self.assertNotIn("format:", y)                 # auto → låt Soup gissa

    def test_format_and_streaming(self):
        y = st.build_yaml(dict(self.FORM, format="alpaca", profile="4gb"))
        self.assertIn("format: alpaca", y)
        self.assertIn("stream_layers: true", y)
        y = st.build_yaml(dict(self.FORM, profile="cpu"))
        self.assertNotIn("quantization:", y)           # cpu-profilen kör utan kvantisering

    def test_seed_and_paths_with_spaces(self):
        y = st.build_yaml(dict(self.FORM, seed="42", data="data/min fil.jsonl"))
        self.assertIn("seed: 42", y)
        self.assertIn('train: "./data/min fil.jsonl"', y)

    def test_absolute_path_kept(self):
        y = st.build_yaml(dict(self.FORM, data="/data/set.jsonl"))
        self.assertIn("train: /data/set.jsonl", y)


class TestValidate(unittest.TestCase):
    OK = {"base": "Qwen/Qwen2.5-0.5B-Instruct", "data": "data/x.jsonl", "task": "sft",
          "format": "auto", "epochs": 3, "lr": "2e-5", "max_length": 1024}

    def test_valid_form(self):
        self.assertEqual(st.validate(self.OK), [])

    def test_each_error(self):
        cases = {
            "base": dict(self.OK, base=""),
            "data": dict(self.OK, data=""),
            "task": dict(self.OK, task="dansa"),
            "format": dict(self.OK, format="excel"),
            "epochs": dict(self.OK, epochs=0),
            "lr": dict(self.OK, lr="5"),               # inte < 1
            "max_length": dict(self.OK, max_length=16),
        }
        for label, form in cases.items():
            self.assertTrue(st.validate(form), label)

    def test_empty_form_lists_everything(self):
        self.assertGreaterEqual(len(st.validate({})), 2)


class TestDataInspection(unittest.TestCase):
    def test_detect_formats(self):
        cases = [
            ([{"instruction": "a", "output": "b"}], "alpaca"),
            ([{"messages": [{"role": "user", "content": "hej"}]}], "chatml"),
            ([{"conversations": [{"from": "human", "value": "hej"}]}], "sharegpt"),
            ([{"prompt": "p", "chosen": "c", "rejected": "r"}], "dpo"),
            ([{"prompt": "p", "completion": "c", "label": True}], "kto"),
            ([{"text": "rå text"}], "text"),
            ([{"vad": "är detta"}], "okänd"),
            ([], "okänd"),
        ]
        for rows, want in cases:
            self.assertEqual(st.detect_format(rows), want, rows)

    def test_inspect_counts_and_problems(self):
        info = st.inspect_jsonl(st.demo_jsonl())
        self.assertEqual(info["rows"], len(st.DEMO_ROWS))
        self.assertEqual(info["format"], "alpaca")
        self.assertTrue(info["examples"][0]["in"])
        self.assertTrue(any("Bara" in p for p in info["problems"]))   # få rader → varning

    def test_inspect_reports_broken_lines(self):
        text = '{"instruction": "a", "output": "b"}\n{trasig\n\n{"instruction": "c", "output": "d"}\n'
        info = st.inspect_jsonl(text)
        self.assertEqual(info["rows"], 2)
        self.assertEqual(len(info["bad_lines"]), 1)
        self.assertEqual(info["bad_lines"][0]["line"], 2)
        self.assertTrue(any("giltig JSON" in p for p in info["problems"]))

    def test_inspect_empty(self):
        info = st.inspect_jsonl("")
        self.assertEqual(info["rows"], 0)
        self.assertTrue(any("inga användbara rader" in p for p in info["problems"]))

    def test_preview_handles_every_format(self):
        self.assertEqual(st.row_preview({"instruction": "Fråga", "input": "x",
                                         "output": "Svar"})["ut"], "Svar")
        self.assertEqual(st.row_preview({"messages": [
            {"role": "user", "content": "Hej"},
            {"role": "assistant", "content": "Hallå"}]})["in"], "Hej")
        self.assertEqual(st.row_preview({"conversations": [
            {"from": "human", "value": "Hej"}, {"from": "gpt", "value": "Hallå"}]})["ut"], "Hallå")
        self.assertEqual(st.row_preview({"prompt": "P", "chosen": "C",
                                         "rejected": "R"})["ut"], "C")
        self.assertIn("text", st.row_preview({"okänd": "text"})["ut"])

    def test_rows_to_jsonl_skips_incomplete(self):
        text = st.rows_to_jsonl([
            {"instruction": "Fråga", "input": "", "output": "Svar"},
            {"instruction": "Bara fråga", "input": "", "output": "   "},   # hoppas över
            {"instruction": "", "input": "", "output": "Bara svar"},       # hoppas över
        ])
        rows = [json.loads(l) for l in text.strip().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["instruction"], "Fråga")

    def test_demo_data_is_valid_jsonl(self):
        for line in st.demo_jsonl().strip().splitlines():
            json.loads(line)


class TestProgressParsing(unittest.TestCase):
    def test_tqdm_line(self):
        got = st.parse_progress(" 42%|████      | 42/100 [00:31<00:42,  1.37it/s]")
        self.assertEqual(got["percent"], 42)
        self.assertEqual((got["step"], got["total"]), (42, 100))
        self.assertEqual(got["eta"], "00:42")

    def test_unknown_eta(self):
        got = st.parse_progress("  0%|          | 0/50 [00:00<?, ?it/s]")
        self.assertIsNone(got["eta"])

    def test_hf_metric_dict(self):
        got = st.parse_progress("{'loss': 1.2345, 'learning_rate': 1.8e-05, 'epoch': 0.36}")
        self.assertAlmostEqual(got["loss"], 1.2345)
        self.assertAlmostEqual(got["lr"], 1.8e-05)
        self.assertAlmostEqual(got["epoch"], 0.36)

    def test_json_metrics_and_eval_loss(self):
        got = st.parse_progress('{"eval_loss": 0.98, "epoch": 2.0}')
        self.assertAlmostEqual(got["eval_loss"], 0.98)

    def test_ansi_is_stripped(self):
        got = st.parse_progress("\x1b[32mStep 5/50\x1b[0m")
        self.assertEqual((got["step"], got["total"], got["percent"]), (5, 50, 10))

    def test_plain_lines_are_not_progress(self):
        for line in ("Auto batch size: 4", "", "   ", None):
            self.assertIsNone(st.parse_progress(line))

    def test_noise_detection(self):
        self.assertTrue(st.is_noise(" 42%|███| 42/100 [00:31<00:42,  1.37it/s]"))
        self.assertTrue(st.is_noise("   "))
        self.assertFalse(st.is_noise("Training complete. Model saved to ./runs/x"))


class TestFailureSummary(unittest.TestCase):
    def test_oom_gets_actionable_advice(self):
        msg = st.summarize_failure(["torch.cuda.OutOfMemoryError: CUDA out of memory"])
        self.assertIn("GPU-minne", msg)

    def test_missing_package(self):
        msg = st.summarize_failure(["ModuleNotFoundError: No module named 'torch'"])
        self.assertIn("soup-cli[train]", msg)

    def test_gated_model(self):
        msg = st.summarize_failure(["401 Client Error: Cannot access gated repo"])
        self.assertIn("Hugging Face", msg)

    def test_generic_error_line_is_returned(self):
        self.assertIn("Error: något gick fel",
                      st.summarize_failure(["rad", "Error: något gick fel"]))

    def test_no_error_found(self):
        self.assertIsNone(st.summarize_failure(["allt gick bra", ""]))


class TestCommands(unittest.TestCase):
    def test_train_and_export_commands(self):
        self.assertEqual(st.train_command("/bin/soup", "/ws/soup.yaml", "/ws/runs/x"),
                         ["/bin/soup", "train", "--config", "/ws/soup.yaml",
                          "--output", "/ws/runs/x"])
        cmd = st.export_command("/bin/soup", "/ws/runs/x", "soup-x")
        self.assertIn("--deploy", cmd)
        self.assertEqual(cmd[cmd.index("--deploy") + 1], "ollama")
        self.assertEqual(cmd[cmd.index("--deploy-name") + 1], "soup-x")

    def test_install_command_uses_this_python(self):
        cmd = st.install_command()
        self.assertEqual(cmd[:4], [sys.executable, "-m", "pip", "install"])
        self.assertIn(st.SOUP_PACKAGE, cmd)

    def test_find_soup_returns_none_when_missing(self):
        self.assertIsNone(st.find_soup("/finns/inte/soup"))


if __name__ == "__main__":
    unittest.main()
