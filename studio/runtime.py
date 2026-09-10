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
    """(modeller, fel) för en instans.

    Felet returneras i stället för att sväljas. Först svalde den allt och gav
    tom lista, vilket gjorde "instansen svarar inte" omöjlig att skilja från
    "inget är laddat" – och urladdningen svarade glatt att kortet var tomt när
    den i själva verket inte hade fått tag i någon."""
    try:
        req = urllib.request.Request(base.rstrip("/") + "/api/ps")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return [], "HTTP %s från %s" % (e.code, base)
    except Exception as e:
        return [], "%s (%s)" % (e, base)
    return ([m for m in (data.get("models") or []) if m.get("name") or m.get("model")],
            "")


def model_name(m):
    return m.get("name") or m.get("model") or ""


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


def actual_gpu(model, gpus):
    """Vilket kort ligger modellen FYSISKT på? None om det inte går att avgöra.

    gpu-fältet i OLLAMA_STUDIO_BACKENDS är en ETIKETT, inte en bindning. Pinnas
    inte instansen (CUDA_VISIBLE_DEVICES) väljer Ollama kort själv – och då kan
    "Mina modeller" säga GPU 0 medan "System / GPU" visar processen på GPU 1.

    Vi matchar modellens VRAM mot Ollama-processernas minne per kort, och påstår
    bara något när exakt EN process på exakt ETT kort passar. Hellre "vet inte"
    än en gissning som ser ut som ett faktum."""
    want_mb = (model.get("size_vram") or 0) / (1024.0 * 1024.0)
    if want_mb <= 0:
        return None
    tol = max(500.0, want_mb * 0.15)
    matches = []
    for g in gpus or []:
        for p in g.get("procs") or []:
            if not p.get("is_ollama"):
                continue
            mem = p.get("mem_mb")
            if mem is not None and abs(mem - want_mb) <= tol:
                matches.append(g.get("index"))
    uniq = set(matches)
    return matches[0] if len(matches) == 1 and len(uniq) == 1 else None


def backends_for_gpu(index):
    """Backends som är låsta till en viss GPU."""
    return [b for b in backends.BACKENDS
            if b.get("gpu") is not None and str(b["gpu"]) == str(index)]


def unload_backend(label):
    """Ladda ur allt i en namngiven instans.

    Behövs när modellen ligger i en annan instans än den som är kopplad till
    kortet man klickade på – då kan UI:t erbjuda att tömma den i stället för att
    bara konstatera att kortet såg tomt ut."""
    target = [b for b in backends.BACKENDS if b["label"] == label]
    if not target:
        return False, {"error": "Ingen backend heter %r." % label,
                       "unloaded": [], "failed": []}
    b = target[0]
    models, err = running_on(b["url"])
    if err:
        return False, {"error": "Kunde inte fråga %s: %s" % (label, err),
                       "unloaded": [], "failed": []}
    unloaded, failed, freed = [], [], 0
    for m in models:
        name = model_name(m)
        ok, uerr = unload_model(b["url"], name)
        if ok:
            unloaded.append(name)
            freed += int(m.get("size_vram") or 0)
        else:
            failed.append({"model": name, "error": uerr})
    return (not failed), {"unloaded": unloaded, "failed": failed,
                          "freed_bytes": freed, "backends": [label],
                          "all_gpus": False, "checked": [], "elsewhere": []}


def _backends_holding_gpu(index):
    """Instanser vars laddade modell fysiskt ligger på GPU `index`."""
    try:
        from .sysinfo import nvidia_gpus
        gpus, _err = nvidia_gpus()
    except Exception:
        return []
    if not gpus:
        return []
    out = []
    for b in backends.BACKENDS:
        models, err = running_on(b["url"])
        if err:
            continue
        for m in models:
            if str(actual_gpu(m, gpus)) == str(index):
                out.append(b)
                break
    # Matchar FLERA instanser samma kort är matchningen tvetydig – två lika stora
    # modeller passar lika bra mot samma process. Då är etiketten det minst dåliga
    # svaret; att ta båda skulle tömma ett kort användaren inte klickade på.
    return out if len(out) == 1 else []


def unload_gpu(index):
    """Ladda ur allt som ligger på en GPU.

    Returnerar (ok, info) där info berättar vad som hände – inklusive om
    urladdningen träffade fler kort än det som klickades på."""
    targets = backends_for_gpu(index)
    all_gpus = False

    # Etiketten kan ljuga. Ligger en modell fysiskt på det här kortet, ta den
    # instans som faktiskt håller den – oavsett vad backenden heter.
    physical = _backends_holding_gpu(index)
    if physical:
        targets = physical
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

    unloaded, failed, freed, checked = [], [], 0, []
    for b in targets:
        models, err = running_on(b["url"])
        checked.append({"label": b["label"], "url": b["url"], "error": err,
                        "reachable": not err,
                        "loaded": [model_name(m) for m in models]})
        for m in models:
            name = model_name(m)
            ok, uerr = unload_model(b["url"], name)
            if ok:
                unloaded.append(name)
                freed += int(m.get("size_vram") or 0)
            else:
                failed.append({"model": name, "error": uerr})

    info = {"unloaded": unloaded, "failed": failed, "freed_bytes": freed,
            "all_gpus": all_gpus, "backends": [b["label"] for b in targets],
            "checked": checked, "elsewhere": []}

    if not unloaded and not failed:
        # Ingenting laddades ur. Säg VARFÖR – annars ser en död instans exakt
        # likadan ut som ett tomt kort, och man letar efter fel sak.
        dead = [c for c in checked if not c["reachable"]]
        if dead:
            info["message"] = ("Kunde inte fråga %s: %s. Kortet kan mycket väl ha en "
                               "modell laddad – vi fick bara inget svar."
                               % (", ".join(c["label"] for c in dead),
                                  dead[0]["error"]))
        else:
            # Instansen svarade, men hade inget. Ligger modellen någon annanstans?
            names = {c["label"] for c in checked}
            for b in backends.BACKENDS:
                if b["label"] in names:
                    continue
                models, err = running_on(b["url"])
                for m in models:
                    info["elsewhere"].append({"backend": b["label"],
                                              "model": model_name(m),
                                              "size_vram": m.get("size_vram")})
            if info["elsewhere"]:
                info["message"] = ("Inget är laddat i %s. Däremot ligger %s i %s – kör "
                                   "Ollama en enda instans för båda korten hamnar allt "
                                   "där, oavsett vilken GPU som används."
                                   % (", ".join(sorted(names)),
                                      ", ".join(e["model"] for e in info["elsewhere"]),
                                      ", ".join(sorted({e["backend"]
                                                        for e in info["elsewhere"]}))))
            else:
                info["message"] = ("Ingen modell är laddad i %s just nu."
                                   % ", ".join(sorted(names)))
    return (not failed), info
