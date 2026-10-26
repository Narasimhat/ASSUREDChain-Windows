"""Import/session/approval metadata regressions; never write or anchor records."""
from __future__ import annotations
import copy
import json
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.components.assured_tools import parse_assured_design_export, apply_design_import_to_session
from app.components.design_enzymes import ENZYMES, build_locked_reagent_spec
from scripts.test_assured_tools import SAMPLE_DESIGN

class DesignEnzymeTests(unittest.TestCase):
    def spec(self, enzyme="mad7"):
        result = copy.deepcopy(SAMPLE_DESIGN)
        name, pam, side = ENZYMES[enzyme]
        result["design"]["enzyme"] = {"id":enzyme,"name":name,"pam_pattern":pam,"pam_side":side,"version":"1.2.0"}
        result["guides"][0]["pam"] = "CTTA" if enzyme == "mad7" else pam
        result["guides"][1]["pam"] = result["guides"][0]["pam"]
        return result

    def lock(self, parsed, **overrides):
        args = dict(project_id="TEST",cas_variant=parsed["cas_variant"],pam_rule=parsed["pam_rule"],
                    guides=parsed["selected_guides"],donors=parsed["donors"],primers=parsed["primer_pairs"],
                    signoff="Test scientist",vendor="Test vendor",notes="Test only",timestamp=1,imported=parsed)
        args.update(overrides)
        return build_locked_reagent_spec(**args)

    def test_all_named_enzymes_preserve_identity_pattern_side_version_through_lock(self):
        for enzyme in ("spcas9","spcas9ng","sacas9","spry","ascas12a","lbcas12a","mad7"):
            with self.subTest(enzyme=enzyme):
                parsed = parse_assured_design_export(self.spec(enzyme))
                locked = self.lock(parsed)
                self.assertEqual(locked["enzyme_id"],enzyme)
                self.assertEqual(locked["cas_variant"],ENZYMES[enzyme][0])
                self.assertEqual(locked["pam_rule"],ENZYMES[enzyme][1])
                self.assertEqual(locked["pam_side"],ENZYMES[enzyme][2])
                self.assertEqual(locked["enzyme_config_version"],"1.2.0")
                self.assertFalse(parsed["enzyme_metadata_assumed"])

    def test_native_result_and_project_bundle_retain_observed_pam(self):
        result = {"type":"ko","gene":"TEST","enzyme":{"id":"mad7","name":"Mad7","version":"1.2.0"},
                  "gs":[{"n":"g1","sp":"ACGT"*5+"ACG","pm":"CTTA","str":"+"}]}
        for payload in (result, {"designResult":result}, {"project":{"options":{"enzyme":"mad7"},"designResult":result}}):
            parsed = parse_assured_design_export(payload)
            self.assertEqual(parsed["cas_variant"],"Mad7")
            self.assertEqual(parsed["pam_rule"],"YTTN")
            self.assertEqual(parsed["selected_guides"][0]["pam"],"CTTA")

    def test_missing_observed_pam_is_unknown_instead_of_fabricated_ngg(self):
        spec = self.spec()
        del spec["guides"][0]["pam"]
        self.assertEqual(parse_assured_design_export(spec)["selected_guides"][0]["pam"],"")

    def test_legacy_default_requires_confirmation_at_lock(self):
        parsed = parse_assured_design_export(copy.deepcopy(SAMPLE_DESIGN))
        self.assertEqual(parsed["cas_variant"],"SpCas9")
        self.assertTrue(parsed["enzyme_metadata_assumed"])
        self.assertTrue(parsed["import_warnings"])
        with self.assertRaisesRegex(ValueError,"Confirm the enzyme"):
            self.lock(parsed)
        self.assertTrue(self.lock(parsed,legacy_confirmed=True)["enzyme_metadata_confirmed"])

    def test_legacy_enzyme_can_be_corrected_only_with_confirmation(self):
        legacy = copy.deepcopy(SAMPLE_DESIGN)
        for guide in legacy["guides"]: guide["pam"] = "CTTA"
        parsed = parse_assured_design_export(legacy)
        with self.assertRaises(ValueError):
            self.lock(parsed,cas_variant="Mad7",pam_rule="YTTN")
        corrected = self.lock(parsed,cas_variant="Mad7",pam_rule="YTTN",legacy_confirmed=True)
        self.assertEqual(corrected["enzyme_id"],"mad7")
        self.assertEqual(corrected["cas_variant"],"Mad7")
        self.assertTrue(all(g["enzyme_id"]=="mad7" for g in corrected["guides"]))
        self.assertIsNone(parsed["selected_guides"][0]["enzyme_id"])

    def test_unknown_conflicting_and_mixed_metadata_are_rejected(self):
        for change in ("unknown","mixed","outer","pam","side","version"):
            with self.subTest(change=change):
                spec = self.spec()
                if change == "unknown": spec["design"]["enzyme"]["id"]="invented"
                elif change == "mixed": spec["guides"][0]["enzyme"]="spcas9"
                elif change == "outer": spec={"options":{"enzyme":"spcas9"},"designResult":spec}
                elif change == "pam": spec["design"]["enzyme"]["pam_pattern"]="NGG"
                elif change == "side": spec["design"]["enzyme"]["pam_side"]="3prime"
                else: spec["guides"][0]["configVersion"]="0.0.1"
                with self.assertRaises(ValueError): parse_assured_design_export(spec)

    def test_recommendation_alternatives_do_not_conflict_with_applied_enzyme(self):
        spec = self.spec()
        spec["design"]["workflowRecommendation"]={"candidates":[{"enzyme":"spcas9"},{"enzyme":"mad7"}]}
        self.assertEqual(parse_assured_design_export(spec)["enzyme_id"],"mad7")

    def test_only_guide_metadata_can_identify_the_enzyme(self):
        spec = self.spec()
        del spec["design"]["enzyme"]
        for guide in spec["guides"]: guide["enzyme"]="mad7"
        self.assertEqual(parse_assured_design_export(spec)["enzyme_id"],"mad7")

    def test_incompatible_observed_pam_cannot_be_approved(self):
        broken = self.spec("spcas9")
        broken["guides"][0]["pam"] = "CTTA"
        with self.assertRaisesRegex(ValueError,"observed guide PAM"):
            self.lock(parse_assured_design_export(broken))

    def test_stale_approval_values_and_empty_guides_cannot_be_locked(self):
        parsed = parse_assured_design_export(self.spec())
        with self.assertRaises(ValueError): self.lock(parsed,cas_variant="SpCas9",pam_rule="NGG")
        with self.assertRaises(ValueError): self.lock(parsed,pam_rule="NGG")
        with self.assertRaises(ValueError): self.lock(parsed,guides=[])
        with self.assertRaises(ValueError): self.lock(parsed,signoff="")

    def test_session_prefill_replaces_cas9_and_scopes_import_to_project(self):
        state={"assured_design_cas":"SpCas9","assured_design_pam":"NGG","guide_pam_0":"NGG"}
        with patch.dict(sys.modules,{"streamlit":types.SimpleNamespace(session_state=state)}):
            apply_design_import_to_session(parse_assured_design_export(self.spec()),project_id="PROJECT_A")
        self.assertEqual(state["assured_design_cas"],"Mad7")
        self.assertEqual(state["assured_design_pam"],"YTTN")
        self.assertEqual(state["guide_pam_0"],"CTTA")
        self.assertEqual(state["assured_design_project"],"PROJECT_A")


class BenchCliEnzymeTests(unittest.TestCase):
    lock = DesignEnzymeTests.lock
    @unittest.skipUnless((ROOT.parent/"assured-bench/test/fixtures/dipk2a-region.gb").exists(),"Bench fixture not installed")
    def test_real_cli_import_and_lock_for_all_seven_enzymes(self):
        raw=(ROOT.parent/"assured-bench/test/fixtures/dipk2a-region.gb").read_text()
        for enzyme in ("spcas9","spcas9ng","sacas9","spry","ascas12a","lbcas12a","mad7"):
            with self.subTest(enzyme=enzyme):
                payload={"gene":"DIPK2A","editType":"ko","genbankText":raw,"options":{"enzyme":enzyme}}
                process=subprocess.run(["node",str(ROOT/"scripts/bench_design_cli.mjs")],input=json.dumps(payload),text=True,capture_output=True,timeout=60)
                self.assertEqual(process.returncode,0,process.stdout+process.stderr)
                output=json.loads(process.stdout)
                self.assertEqual(len(output["designResult"]["gs"]),2)
                for exported in (output["qcDesignSpec"],output):
                    parsed=parse_assured_design_export(exported)
                    locked=self.lock(parsed)
                    self.assertEqual(locked["enzyme_id"],enzyme)
                    self.assertEqual(locked["pam_rule"],ENZYMES[enzyme][1])
                    self.assertEqual(parsed["selected_guides"][0]["pam"],output["designResult"]["gs"][0]["pm"])
                    self.assertFalse(parsed["enzyme_metadata_assumed"])
                if enzyme=="spcas9":
                    self.assertGreater(output["designResult"]["koAssessment"]["outcomes"]["minLofPct"],85)

if __name__=="__main__": unittest.main()
