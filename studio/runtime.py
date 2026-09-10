"""Modeller som ligger laddade just nu, och att ladda ur dem.

Ollama håller en modell i VRAM tills keep_alive löper ut. Vill man ha kortet
ledigt – för ett spel, en annan modell, eller bara för att se att det är tomt –
finns inget annat sätt än att be instansen släppa den. Det görs med keep_alive 0.

Vilken GPU en modell hamnar på bestäms av vilken Ollama-instans som kör den:
körs en instans per GPU (OLLAMA_STUDIO_BACKENDS) går det att träffa exakt ett
kort. Kör EN instans för alla GPU:er går det inte att skilja korten åt – då
laddas allt ur, och det ska sägas rakt ut i stället för att låtsas annat.
"""
import json
import urllib.error
import urllib.request

from . import backends

UNLOAD_TIMEOUT = 20


def _post(base, path, payload, timeout):
    req = urllib.request.Request(base.rstrip("/") + path,
                                 data=json.dumps(payload).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace") or "{}")


def running_on(base, timeout=3):
    """Modeller som är laddade i en instans: [{name, size_vram}, …]."""
    try:
        req = urllib.request.Request(base.rstrip("/") + "/api/ps")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return []
    return [m for m in (data.get("models") or []) if m.get("name") or m.get("model")]


def unload_model(base, name, timeout=UNLOAD_TIMEOUT):
    """Be Ollama släppa en modell ur minnet. Returnerar (ok, felmeddelande)."""
    try:
        # keep_alive 0 = lägg ifrån dig den nu. Utan prompt görs ingen generering.
        _post(base, "/api/generate", {"model": name, "keep_alive": 0}, timeout)
        return True, ""
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace") or "{}")
            return False, body.get("error") or ("HTTP %s" % e.code)
        except Exception:
            return False, "HTTP %s" % e.code
    except Exception as e:
        return False, str(e)


def backends_for_gpu(index):
    """Backends som är låsta till en viss GPU."""
    return [b for b in backends.BACKENDS
            if b.get("gpu") is not None and str(b["gpu"]) == str(index)]


def unload_gpu(index):
    """Ladda ur allt som ligger på en GPU.

    Returnerar (ok, info) där info berättar vad som hände – inklusive om
    urladdningen träffade fler kort än det som klickades på."""
    targets = backends_for_gpu(index)
    all_gpus = False
    if not targets:
        # Ingen instans är låst till just det här kortet. Kör en enda instans
        # allt är det ändå den som håller modellen – men då lossnar alla kort.
        if len(backends.BACKENDS) == 1:
            targets = list(backends.BACKENDS)
            all_gpus = True
        else:
            return False, {"error": "Ingen Ollama-instans är kopplad till GPU %s. "
                                    "Sätt OLLAMA_STUDIO_BACKENDS för att styra kort "
                                    "för kort." % index,
                           "unloaded": [], "failed": []}

    unloaded, failed, freed = [], [], 0
    for b in targets:
        for m in running_on(b["url"]):
            name = m.get("name") or m.get("model")
            ok, err = unload_model(b["url"], name)
            if ok:
                unloaded.append(name)
                freed += int(m.get("size_vram") or 0)
            else:
                failed.append({"model": name, "error": err})
    return (not failed), {"unloaded": unloaded, "failed": failed,
                          "freed_bytes": freed, "all_gpus": all_gpus,
                          "backends": [b["label"] for b in targets]}
