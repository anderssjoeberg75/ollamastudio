import json
import os
import tempfile
import unittest
from unittest.mock import patch

from studio.harness import skills as S
from studio.harness.engine import VulnerabilityHarnessRunner


class TestSecuritySkillsCatalog(unittest.TestCase):

    def test_catalog_loads_all_vendored_skills(self):
        cat = S.load_catalog()
        self.assertEqual(len(cat), 67)
        self.assertEqual(sum(1 for v in cat.values() if v["source"] == "acs"), 22)
        scr = cat["secure-code-review"]
        self.assertEqual(scr["domain"], "appsec")
        self.assertIn("OWASP-ASVS", scr["frameworks"])
        self.assertTrue(scr["sections"])

    def test_minimal_frontmatter_parser_without_yaml(self):
        text = "---\nname: x\ndescription: >\n  hello\n  world\nframeworks: [A, B]\n---\n# body\n"
        with patch.dict("sys.modules", {"yaml": None}):
            fm, body = S._parse_frontmatter(text)
        self.assertEqual(fm["name"], "x")
        self.assertEqual(fm["description"], "hello world")
        self.assertEqual(fm["frameworks"], ["A", "B"])
        self.assertIn("# body", body)

    def test_minimal_parser_block_lists(self):
        text = "---\nname: y\ntags:\n- a\n- 'b'\nmitre_attack:\n  - T1190\nversion: '1.0'\n---\nx\n"
        with patch.dict("sys.modules", {"yaml": None}):
            fm, _ = S._parse_frontmatter(text)
        self.assertEqual(fm["tags"], ["a", "b"])
        self.assertEqual(fm["mitre_attack"], ["T1190"])
        self.assertEqual(fm["version"], "1.0")

    def test_list_skills_marks_code_audit_domains(self):
        items = S.list_skills()
        self.assertEqual(len(items), 67)
        by = {i["name"]: i for i in items}
        self.assertTrue(by["api-security"]["code_audit"])
        self.assertFalse(by["ir-playbook"]["code_audit"])


class TestSpecialistSkills(unittest.TestCase):

    def test_acs_skills_have_frameworks_and_domain(self):
        cat = S.load_catalog()
        for name, sk in cat.items():
            if sk["source"] != "acs":
                continue
            self.assertTrue(sk["frameworks"], name)
            self.assertIn(sk["domain"], S.CODE_AUDIT_DOMAINS, name)
            self.assertTrue(sk["sections"], name)

    def test_no_scripts_vendored(self):
        for root, _dirs, files in os.walk(S.ACS_ROOT):
            for f in files:
                self.assertTrue(f.endswith(".md") or f == "LICENSE", os.path.join(root, f))

    def test_every_trigger_points_at_a_vendored_skill(self):
        cat = S.load_catalog()
        for name, _regexes, _exts in S.SPECIALIST_TRIGGERS:
            self.assertIn(name, cat)
            self.assertEqual(cat[name]["source"], "acs")

    def test_specialists_triggered_by_code_signals(self):
        pick = lambda p, c, n=3: S.select_skills_for_unit({"path": p, "attack_class": "x"}, c, max_skills=n)
        self.assertIn("exploiting-prototype-pollution-in-javascript", pick("a.js", "_.merge({}, req.body)"))
        self.assertNotIn("exploiting-prototype-pollution-in-javascript", pick("a.py", "_.merge({}, x)"))
        self.assertIn("exploiting-type-juggling-vulnerabilities", pick("a.php", "if ($h == '0e1') {}"))
        self.assertIn("testing-jwt-token-security", pick("a.py", "import jwt\njwt.decode(t, verify=False)"))
        self.assertIn("auditing-mcp-servers-for-tool-poisoning", pick("srv.py", "@mcp.tool()\ndef f(): pass"))
        self.assertIn("testing-for-open-redirect-vulnerabilities",
                      pick("v.py", "return redirect(request.args.get('next'))"))
        # Båda villkoren krävs för open redirect.
        self.assertNotIn("testing-for-open-redirect-vulnerabilities", pick("v.py", "return redirect('/home')"))

    def test_new_file_kinds(self):
        pick = lambda p: S.select_skills_for_unit({"path": p, "attack_class": "x"})
        self.assertEqual(pick("contracts/Token.sol")[0], "auditing-foundry-smart-contract-security")
        self.assertEqual(pick("app/src/main/AndroidManifest.xml")[0], "performing-android-app-static-analysis-with-mobsf")
        self.assertEqual(pick("ios/App/Info.plist")[0], "performing-ios-app-security-assessment")
        self.assertEqual(pick("serverless.yml")[0], "performing-serverless-function-security-review")

    def test_specialist_comes_right_after_primary_skill(self):
        names = S.select_skills_for_unit(
            {"path": "api/routes.py", "attack_class": "Injection & Input Validation"},
            "@app.get('/x')\nimport jwt", max_skills=2)
        self.assertEqual(names, ["secure-code-review", "testing-jwt-token-security"])

    def test_acs_digest_drops_tooling_sections(self):
        d = S.skill_digest("testing-jwt-token-security", ["token"], budget=5000)
        self.assertIn("SKILL: testing-jwt-token-security", d)
        self.assertNotIn("#### Prerequisites", d)
        self.assertNotIn("#### Tools & Systems", d)
        self.assertLess(len(d), 5600)

    def test_acs_envelope_validates(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        cand = TestNormalizedExport()._cand(skills_applied=["testing-jwt-token-security"], cwe="CWE-347",
                                            framework_refs=[])
        envs = S.build_normalized_envelopes("vh-acs", "p", [cand])
        env = envs["testing-jwt-token-security"]
        jsonschema.validate(env, S.finding_schema())
        self.assertIn("MITRE-ATT&CK", env["skill"]["frameworks"])


class TestAllSkillsMode(unittest.TestCase):
    """Alla skills aktiva: budget och kontextfönster ska hålla, relevans ska styra."""

    ROUTE = "@app.get('/u')\nimport jwt\ndef u():\n    cursor.execute('select ' + request.args['q'])\n"

    def test_budget_is_strict_even_with_all_skills_active(self):
        for prof, budget in S.PROFILE_BUDGETS.items():
            pool = set(S.load_catalog())
            names, text = S.build_skill_context(
                {"path": "api/routes.py", "attack_class": "Injection & Input Validation"},
                self.ROUTE, profile=prof, rotation_pool=pool)
            self.assertLessEqual(len(text), budget + 400, prof)
            self.assertLessEqual(len(names), 4 + S.ROTATION_SLOTS[prof])

    def test_digest_keeps_real_checklist_content(self):
        pool = set(S.load_catalog())
        _names, text = S.build_skill_context(
            {"path": "api/routes.py", "attack_class": "Injection & Input Validation"},
            self.ROUTE, profile="standard", rotation_pool=pool)
        self.assertIn("V5.3.4", text)          # kontrolltabell, inte bara rubriker

    def test_skill_digest_never_exceeds_budget(self):
        for name in S.load_catalog():
            self.assertLessEqual(len(S.skill_digest(name, ["injection"], budget=600)), 600, name)

    def test_rotation_consumes_pool_and_respects_triggers(self):
        cat = S.load_catalog()
        pool = set(cat)
        names, _ = S.build_skill_context({"path": "web/app.js", "attack_class": "x"},
                                         "const t = 'x';", profile="deep", rotation_pool=pool)
        for n in names:
            self.assertNotIn(n, pool)
        # PHP-specifika och mobila specialister får aldrig roteras in på en JS-fil utan signal.
        self.assertNotIn("exploiting-type-juggling-vulnerabilities", names)
        self.assertNotIn("performing-ios-app-security-assessment", names)
        self.assertIn("exploiting-type-juggling-vulnerabilities", pool)

    def test_rotation_adds_triggered_specialists_beyond_core(self):
        pool = set(S.load_catalog())
        content = ("@app.get('/x')\nimport jwt\nfrom flask import redirect\n"
                   "def x():\n    return redirect(request.args.get('next'))\n# redirect_uri code_verifier\n")
        names, _ = S.build_skill_context({"path": "api/x.py", "attack_class": "Injection & Input Validation"},
                                         content, profile="quick", rotation_pool=pool)
        self.assertIn("testing-jwt-token-security", names)
        self.assertGreaterEqual(len(names), 3)

    def test_start_uses_all_mode_without_stuffing_extras(self):
        from studio.harness import engine
        with patch.object(engine.VulnerabilityHarnessRunner, "start"), \
                patch.object(engine, "init_harness_db"), patch.object(engine, "sqlite3"):
            rid = engine.start_vulnerability_harness("p", use_skills=True)
            runner = engine._active_runners.pop(rid)
        self.assertTrue(runner.apply_all_skills)
        self.assertEqual(runner.extra_skills, [])
        with patch.object(engine.VulnerabilityHarnessRunner, "start"), \
                patch.object(engine, "init_harness_db"), patch.object(engine, "sqlite3"):
            rid = engine.start_vulnerability_harness("p", extra_skills=["pci-dss-review", "nope"])
            runner = engine._active_runners.pop(rid)
        self.assertFalse(runner.apply_all_skills)
        self.assertEqual(runner.extra_skills, ["pci-dss-review"])

    @patch("studio.harness.engine._llm_chat")
    def test_engine_prompt_stays_bounded_in_all_mode(self, mock_llm):
        mock_llm.return_value = ("[]", 10)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "routes.py"), "w") as fh:
                fh.write(self.ROUTE)
            runner = VulnerabilityHarnessRunner(run_id="t-all", project="p", profile="deep")
            runner._sync_db = lambda: None
            runner.skill_rotation_pool = set(S.load_catalog())
            runner.skills_available = len(runner.skill_rotation_pool)
            self.assertTrue(runner.apply_all_skills)
            runner._execute_vdh_hunting(d, [{"unit_id": "u1", "path": "routes.py",
                                             "attack_class": "Injection & Input Validation"}])
        prompt = mock_llm.call_args[0][0]
        self.assertLess(len(prompt), S.PROFILE_BUDGETS["deep"] + 400 + 8000 + 1000)
        self.assertLess(len(runner.skills_used), 10)


class TestSkillSelection(unittest.TestCase):

    def test_infra_files_get_matching_skills(self):
        pick = lambda p: S.select_skills_for_unit({"path": p, "attack_class": "x"})
        self.assertEqual(pick("Dockerfile")[0], "container-security")
        self.assertEqual(pick(".github/workflows/ci.yml")[0], "pipeline-security")
        self.assertEqual(pick("infra/main.tf")[0], "iac-security")
        self.assertEqual(pick("requirements.txt")[0], "dependency-scanning")

    def test_route_and_llm_code(self):
        content = "@app.post('/chat')\ndef chat():\n    openai.chat.completions.create(tools=tools)\n"
        names = S.select_skills_for_unit(
            {"path": "app/api.py", "attack_class": "Injection & Input Validation"}, content, max_skills=4)
        self.assertEqual(names[0], "secure-code-review")
        self.assertIn("api-security", names)
        self.assertIn("prompt-injection", names)
        self.assertIn("agent-security", names)

    def test_extra_skills_appended_and_unknown_ignored(self):
        names = S.select_skills_for_unit({"path": "a.py", "attack_class": "x"}, extra_skills=["pci-dss-review", "nope"])
        self.assertIn("pci-dss-review", names)
        self.assertNotIn("nope", names)

    def test_digest_respects_budget_and_drops_report_templates(self):
        for prof, budget in S.PROFILE_BUDGETS.items():
            names, text = S.build_skill_context(
                {"path": "api/routes.py", "attack_class": "Injection & Input Validation"},
                "cursor.execute(q)", profile=prof)
            self.assertTrue(names)
            self.assertLess(len(text), budget + 1200)
        d = S.skill_digest("secure-code-review", ["injection"], budget=4000)
        self.assertIn("V5.3.4", d)
        self.assertNotIn("$ARGUMENTS", d)
        self.assertNotIn("#### Output Format", d)


class TestNormalizedExport(unittest.TestCase):

    def _cand(self, **kw):
        c = {
            "id": "CAND-1", "title": "[SEC-1] SQL injection", "file": "api/routes.py", "line": 12,
            "severity": "high", "cwe": "CWE-89 (SQL Injection)", "problem": "raw query",
            "source_to_sink": "req.args -> cursor.execute", "fix": "parameterize",
            "framework_refs": ["OWASP-ASVS V5.3.4"], "skills_applied": ["secure-code-review"],
            "poc": "curl 'http://x/?q=1%27--'",
            "vvs_validation": {"calculated_confidence": 0.9},
        }
        c.update(kw)
        return c

    def test_parse_framework_ref(self):
        self.assertEqual(S.parse_framework_ref("OWASP-ASVS V5.3.4"), {"framework": "OWASP-ASVS", "control": "V5.3.4"})
        self.assertEqual(S.parse_framework_ref("LLM01:2025 Prompt Injection")["control"], "LLM01:2025")

    def test_envelopes_validate_against_upstream_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema = S.finding_schema()
        confirmed = [self._cand(), self._cand(id="CAND-2", cwe=None, framework_refs=None,
                                              skills_applied=["container-security"], file="Dockerfile")]
        rejected = [self._cand(id="CAND-3", cwe="", framework_refs=[], skills_applied=[])]
        envs = S.build_normalized_envelopes("vh-test", "owner/repo", confirmed, rejected)
        self.assertIn("secure-code-review", envs)
        self.assertIn("container-security", envs)
        for env in envs.values():
            jsonschema.validate(env, schema)
        statuses = [f["status"] for f in envs["secure-code-review"]["findings"]]
        self.assertIn("false_positive", statuses)


class TestEngineIntegration(unittest.TestCase):

    @patch("studio.harness.engine._llm_chat")
    def test_vdh_prompt_contains_skill_playbooks(self, mock_llm):
        mock_llm.return_value = (json.dumps([{
            "title": "SQLi", "problem": "raw", "skill": "owasp-top-10-web",
            "framework_refs": "OWASP-ASVS V5.3.4"}]), 100)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "api"))
            with open(os.path.join(d, "api", "routes.py"), "w") as fh:
                fh.write("@app.get('/u')\ndef u():\n    cursor.execute('select ' + request.args['q'])\n")
            runner = VulnerabilityHarnessRunner(run_id="t-skills", project="p", profile="quick")
            units = [{"unit_id": "unit-1", "path": "api/routes.py", "attack_class": "Injection & Input Validation"}]
            cands = runner._execute_vdh_hunting(d, units)
        prompt = mock_llm.call_args[0][0]
        self.assertIn("SECURITY SKILL PLAYBOOKS", prompt)
        self.assertIn("SKILL: secure-code-review", prompt)
        # quick-profilen väljer secure-code-review + api-security; modellens 'skill' finns inte där.
        self.assertEqual(cands[0]["skills_applied"], units[0]["skills"])
        self.assertEqual(cands[0]["framework_refs"], ["OWASP-ASVS V5.3.4"])
        self.assertGreater(runner.skills_used["secure-code-review"], 0)

    @patch("studio.harness.engine._llm_chat")
    def test_skills_can_be_disabled(self, mock_llm):
        mock_llm.return_value = ("[]", 10)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.py"), "w") as fh:
                fh.write("x = 1\n")
            runner = VulnerabilityHarnessRunner(run_id="t-noskills", project="p", use_skills=False)
            runner._execute_vdh_hunting(d, [{"unit_id": "u", "path": "a.py", "attack_class": "x"}])
        self.assertNotIn("SECURITY SKILL PLAYBOOKS", mock_llm.call_args[0][0])
        self.assertEqual(runner.skills_used, {})

    def test_ledger_covers_infra_files(self):
        runner = VulnerabilityHarnessRunner(run_id="t-ledger", project="p")
        units = runner._build_coverage_ledger(["Dockerfile", ".github/workflows/ci.yml", "requirements.txt",
                                               "contracts/Vault.sol", "AndroidManifest.xml", "serverless.yml"])
        classes = [u["attack_class"] for u in units]
        self.assertIn("Smart Contract Security", classes)
        self.assertIn("Mobile App Security", classes)
        self.assertIn("Serverless Function Security", classes)
        self.assertIn("Container & Orchestration Hardening", classes)
        self.assertIn("CI/CD Pipeline & Supply Chain", classes)
        self.assertIn("Dependency & Supply-Chain Risk", classes)

    def test_collect_includes_workflows_and_dockerfiles(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".github", "workflows"))
            os.makedirs(os.path.join(d, ".hidden"))
            for p in (".github/workflows/ci.yml", "Dockerfile", ".hidden/x.py", "app.py"):
                with open(os.path.join(d, p), "w") as fh:
                    fh.write("x")
            files = VulnerabilityHarnessRunner(run_id="t-c", project="p")._collect_source_files(d)
        self.assertIn(".github/workflows/ci.yml", files)
        self.assertIn("Dockerfile", files)
        self.assertIn("app.py", files)
        self.assertNotIn(".hidden/x.py", files)

    def test_save_artifacts_writes_normalized_findings(self):
        with tempfile.TemporaryDirectory() as d:
            runner = VulnerabilityHarnessRunner(run_id="t-art", project="p")
            runner._sync_db = lambda: None
            runner.skills_used = {"secure-code-review": 2}
            runner.confirmed = [TestNormalizedExport()._cand()]
            runner._save_artifacts(d)
            out = os.path.join(d, ".vulnerability-harness", "securityskills", "secure-code-review.json")
            self.assertTrue(os.path.exists(out))
            with open(os.path.join(d, ".vulnerability-harness", "REPORT.md")) as fh:
                report = fh.read()
            self.assertIn("Security Skills Applied", report)
            self.assertIn("OWASP-ASVS V5.3.4", report)


if __name__ == "__main__":
    unittest.main()
