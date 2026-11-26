"""Your company profile (Settings) and why each prospect fits it (Outbox).

The profile is the file the Writer already reads; the dashboard now edits
it. A fit is worked out once per company, shown on the draft, given to the
Writer, and cleared when the profile changes.
"""

import asyncio
from pathlib import Path

import pytest

import harvey.brain as brain_mod
import harvey.dashboard as dash
from harvey.fit import build_fit_prompt, cached_fit, fit_lines, parse_fit, starter_profile
from tests.test_outbox_reasons import _seed, client  # noqa: F401  (fixture)

PROFILE = "# Example Twins\n\n## What we do\nWe build 3D digital twins of buildings and sites.\n"


@pytest.fixture
def skills(monkeypatch, tmp_path):
    monkeypatch.setattr(brain_mod, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(dash, "_resolve_config_file",
                        lambda: Path(__file__).resolve().parent.parent / "harvey.yaml")
    return tmp_path


def _fake_fit(monkeypatch, seen):
    async def think_json(self, prompt, **kw):
        seen.append(prompt)
        return {"summary": "An MEP contractor.", "fit": "Digital twins help hand over MEP systems.",
                "angle": "Ask how they hand over as-built MEP.", "score": "strong"}
    monkeypatch.setattr("harvey.brain.Brain.think_json", think_json)


def test_a_new_install_gets_a_starter_profile_to_fill_in(client, skills):
    d = client.get("/api/company-profile").json()
    assert d["exists"] is False and "## What we do" in d["text"]


def test_saving_the_profile_writes_the_file_the_writer_reads(client, skills):
    r = client.post("/api/company-profile", json={"text": PROFILE})
    assert r.json()["success"]
    assert (skills / "product_knowledge.md").read_text().startswith("# Example Twins")
    d = client.get("/api/company-profile").json()
    assert d["exists"] and "digital twins" in d["text"]
    assert client.post("/api/company-profile", json={"text": "short"}).status_code == 400


def test_fit_needs_a_profile_first(client, skills):
    _, items = asyncio.run(_seed(client.db_path))
    r = client.post(f"/api/outbox/{items[('ops', 1)]}/fit", json={})
    assert r.status_code == 409 and "Your company" in r.json()["message"]


def test_fit_is_worked_out_once_per_company_and_shown_on_drafts(client, skills, monkeypatch):
    state, items = asyncio.run(_seed(client.db_path))
    client.post("/api/company-profile", json={"text": PROFILE})
    seen = []
    _fake_fit(monkeypatch, seen)
    r = client.post(f"/api/outbox/{items[('ops', 1)]}/fit", json={})
    assert r.json()["fit"]["score"] == "strong" and not r.json()["cached"]
    assert "3D digital twins" in seen[0] and "<untrusted" in seen[0]
    # a colleague at the same company: cached, no second call
    r = client.post(f"/api/outbox/{items[('inbox', 1)]}/fit", json={})
    assert r.json()["cached"] and len(seen) == 1
    pending = client.get("/api/outbox").json()["pending"]
    assert all(p["context"]["fit"]["fit"].startswith("Digital twins") for p in pending
               if p["context"]["company"] == "Acme Fitouts")
    # refresh asks again
    client.post(f"/api/outbox/{items[('ops', 1)]}/fit", json={"refresh": True})
    assert len(seen) == 2


def test_changing_the_profile_clears_every_fit(client, skills, monkeypatch):
    state, items = asyncio.run(_seed(client.db_path))
    client.post("/api/company-profile", json={"text": PROFILE})
    _fake_fit(monkeypatch, [])
    client.post(f"/api/outbox/{items[('ops', 1)]}/fit", json={})
    r = client.post("/api/company-profile", json={"text": PROFILE + "\n## Services\n- Scan to BIM\n"})
    assert r.json()["fits_cleared"] == 1
    pid_company = client.get("/api/outbox").json()["pending"][0]["context"]["company_id"]
    assert asyncio.run(cached_fit(state, pid_company)) is None


def test_parse_fit_and_writer_lines():
    assert parse_fit({"summary": "", "fit": "x"}) is None
    fit = parse_fit({"summary": "s", "fit": "f", "angle": "a", "score": "AMAZING"})
    assert fit["score"] == "possible"
    assert "not a fact to quote" in fit_lines(fit)[0]
    assert fit_lines(None) == []
    assert "## Services" in starter_profile(None)
    prompt = build_fit_prompt("P" * 50, {"company": "Acme"})
    assert "Acme" in prompt and "Never invent facts" in prompt
