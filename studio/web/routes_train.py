"""Rutter för AI-träning.

Startar och följer träningskörningar, och hanterar dataset på disk."""
import json
import os

from .base import BaseHandler
from studio import backends as _backends
from studio.config import (
    APP_DIR, hf_token, prefs_set, soup_binary, train_resolve,
    train_workspace_root)
from studio.training import (
    TRAIN, _soup_version_cache, train_job_current, train_job_start)


TRAIN_DATASET_WRITE_CAP = 16 * 1024 * 1024
TRAIN_DATASET_READ_CAP = 8 * 1024 * 1024


class TrainRoutes(BaseHandler):
    """Rutter för AI-träning."""

    # ---- AI-träning: dataset, konfig, körningar --------------------------
    def _train_post(self, path, data):
        """POST-åtgärderna bakom /api/train/… Kastar ValueError vid indatafel."""
        action = path[len("/api/train/"):]

        if action == "dataset":
            return self._send_json(self._train_dataset(data))

        if action == "config":
            form = data.get("form") or {}
            errors = TRAIN.validate(form) if data.get("strict") else []
            yaml_text = TRAIN.build_yaml(form)
            if data.get("save"):
                prefs_set({"train_form": json.dumps(form, ensure_ascii=False)})
                cfg = train_resolve("soup.yaml", create=True)
                with open(cfg, "w", encoding="utf-8") as fh:
                    fh.write(yaml_text)
            return self._send_json({"yaml": yaml_text, "errors": errors})

        if action == "start":
            form = data.get("form") or {}
            errors = TRAIN.validate(form)
            if errors:
                return self._send_json({"error": errors[0], "errors": errors}, 400)
            binary = soup_binary()
            if not binary:
                return self._send_json({"error": "Soup är inte installerat på servern."}, 400)
            root = train_workspace_root(create=True)
            if not root:
                return self._send_json({"error": "Kunde inte skapa träningsmappen."}, 500)
            dataset = train_resolve(form.get("data") or "")
            if not os.path.isfile(dataset):
                return self._send_json({"error": "Datafilen finns inte: %s"
                                                 % form.get("data")}, 400)
            cfg_path = train_resolve("soup.yaml", create=True)
            with open(cfg_path, "w", encoding="utf-8") as fh:
                fh.write(TRAIN.build_yaml(form))
            prefs_set({"train_form": json.dumps(form, ensure_ascii=False)})
            name = TRAIN.safe_name(form.get("name") or "min-modell")
            out_dir = train_resolve("runs/" + name, create=True)
            cmd = TRAIN.train_command(binary, cfg_path, out_dir)
            job, err = train_job_start("train", cmd, root, label="Tränar " + name,
                                       env={"HF_TOKEN": hf_token()})
            if err:
                return self._send_json({"error": err}, 409)
            return self._send_json({"ok": True, "job": job.snapshot()})

        if action == "export":
            run = TRAIN.safe_name(data.get("run") or "")
            if not run:
                return self._send_json({"error": "Ingen körning vald."}, 400)
            binary = soup_binary()
            if not binary:
                return self._send_json({"error": "Soup är inte installerat på servern."}, 400)
            model_dir = train_resolve("runs/" + run)
            if not os.path.isdir(model_dir):
                return self._send_json({"error": "Körningen finns inte: %s" % run}, 400)
            ollama_name = TRAIN.ollama_model_name(run)
            cmd = TRAIN.export_command(binary, model_dir, ollama_name)
            job, err = train_job_start("export", cmd, train_workspace_root(create=True),
                                       label="Exporterar " + run,
                                       env={"HF_TOKEN": hf_token(),
                                            "OLLAMA_HOST": _backends.PRIMARY["url"]})
            if err:
                return self._send_json({"error": err}, 409)
            return self._send_json({"ok": True, "job": job.snapshot(),
                                    "ollama_name": ollama_name})

        if action == "install":
            if soup_binary():
                return self._send_json({"error": "Soup är redan installerat."}, 400)
            cmd = TRAIN.install_command()
            job, err = train_job_start("install", cmd, APP_DIR,
                                       label="Installerar " + TRAIN.SOUP_PACKAGE)
            if err:
                return self._send_json({"error": err}, 409)
            _soup_version_cache.update({"path": None, "version": None, "at": 0})
            return self._send_json({"ok": True, "job": job.snapshot()})

        if action == "stop":
            job = train_job_current()
            if not job or not job.running():
                return self._send_json({"error": "Ingen körning pågår."}, 400)
            job.stop()
            return self._send_json({"ok": True})

        return self._send_json({"error": "okänd åtgärd"}, 404)

    def _train_dataset(self, data):
        """Skapa, spara eller granska en datafil i träningsmappen."""
        action = (data.get("action") or "").strip()
        name = (data.get("name") or "").strip()
        if action == "demo":
            name = name or "exempeldata.jsonl"
            text = TRAIN.demo_jsonl()
        elif action == "save":
            text = TRAIN.rows_to_jsonl(data.get("rows") or [])
            if not text.strip():
                raise ValueError("Fyll i minst en rad med både fråga och svar.")
        elif action == "paste":
            text = data.get("content") or ""
            if not text.strip():
                raise ValueError("Klistra in minst en rad JSONL.")
        else:
            raise ValueError("okänd åtgärd: %s" % action)

        filename = TRAIN.safe_name(name or "mitt-dataset", "mitt-dataset")
        if not filename.endswith(".jsonl"):
            filename += ".jsonl"
        if len(text.encode("utf-8")) > TRAIN_DATASET_WRITE_CAP:
            raise ValueError("Datafilen är för stor för att sparas via webbläsaren "
                             "(max %d MB). Lägg den i mappen data/ på servern i stället."
                             % (TRAIN_DATASET_WRITE_CAP // (1024 * 1024)))
        full = train_resolve("data/" + filename, create=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        info = TRAIN.inspect_jsonl(text)
        info.update({"path": "data/" + filename, "name": filename, "saved": True})
        return info
