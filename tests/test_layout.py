"""Guardrails on the skills/ tree itself: it ships only what an agent needs at runtime."""
import ast
import re
import sys
import unittest

from helpers import SKILLS, run

# Runtime adapters that call a model; everything else must be stdlib-only.
MODEL_ADAPTERS = {"2-build-create-evals/target_example.py": {"anthropic"}, "2-build-review-tools/target_tool_choice.py": {"anthropic"}}
# Skills referenced by "(use X)" that live outside this repo.
EXTERNAL_SKILLS = {"brainstorming", "systematic-debugging", "test-driven-development", "writing-skills", "security-review"}
NETWORK = {"urllib", "http", "socket", "requests", "httpx", "ftplib", "smtplib", "aiohttp"}
TESTY = re.compile(r"(^test_.*|.*_test\.py$|^conftest\.py$|.*\.(pyc|jsonl|log)$)")
OS_NOISE = {".DS_Store"}  # recreated by Finder, gitignored, never shipped
TESTY_DIRS = {"tests", "test", "fixtures", "__pycache__", "testdata"}


def skill_dirs():
    return sorted(p for p in SKILLS.iterdir() if p.is_dir())


def frontmatter(md):
    m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
    if not m:
        return None
    return dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line), m.group(1)


class Layout(unittest.TestCase):
    def test_no_test_code_or_artifacts_in_skills(self):
        bad = [str(p.relative_to(SKILLS)) for p in SKILLS.rglob("*")
               if (p.is_dir() and p.name in TESTY_DIRS) or (p.is_file() and TESTY.match(p.name))]
        self.assertEqual(bad, [], "test code / fixtures / build artifacts belong in tests/, not skills/")

    def test_only_skill_files(self):
        for d in skill_dirs():
            for p in d.rglob("*"):
                if p.is_file() and p.name not in OS_NOISE:
                    self.assertIn(p.suffix, {".md", ".py"}, f"unexpected file in skills/: {p.relative_to(SKILLS)}")
            self.assertTrue((d / "SKILL.md").exists(), f"{d.name} has no SKILL.md")

    def test_frontmatter(self):
        names = {d.name for d in skill_dirs()}
        for d in skill_dirs():
            fm = frontmatter((d / "SKILL.md").read_text())
            self.assertIsNotNone(fm, f"{d.name}: no frontmatter")
            fields, raw = fm
            self.assertEqual(set(fields), {"name", "description"}, f"{d.name}: only name and description allowed")
            self.assertEqual(fields["name"], d.name)
            self.assertRegex(fields["name"], r"^[a-z0-9-]+$")
            self.assertRegex(fields["description"], r"^Use (when|before)\b", f"{d.name}: description must start with triggers")
            self.assertLessEqual(len(raw), 1024, f"{d.name}: frontmatter over 1024 chars")
            for ref in re.findall(r"\(use ([a-z0-9-]+)\)", fields["description"]):
                self.assertIn(ref, names | EXTERNAL_SKILLS, f"{d.name}: description points at unknown skill '{ref}'")

    def test_names_follow_stage_scheme(self):
        # <n>-<stage>-<what you are doing>, so the list sorts by lifecycle and the name alone says when to use it
        stages = {"0": None, "1": "plan", "2": "build", "3": "test", "4": "ship", "5": "prod", "6": "fix"}
        for d in skill_dirs():
            m = re.match(r"^(\d)-([a-z]+)-[a-z0-9-]+$", d.name)
            self.assertIsNotNone(m, f"{d.name}: name must be <stage number>-<stage>-<verb phrase>")
            num, stage = m.groups()
            self.assertIn(num, stages, f"{d.name}: unknown stage number")
            if stages[num]:
                self.assertEqual(stage, stages[num], f"{d.name}: stage {num} is '{stages[num]}'")

    def test_skill_md_at_most_500_words(self):
        # loaded into context whenever the skill fires; detail belongs in the script's --help (same count as wc -w)
        for d in skill_dirs():
            n = len((d / "SKILL.md").read_text().split())
            self.assertLessEqual(n, 500, f"{d.name}/SKILL.md is {n} words")

    def test_script_references_resolve_and_no_orphans(self):
        for d in skill_dirs():
            md = (d / "SKILL.md").read_text()
            for ref in re.findall(r"<this-skill-dir>/([\w.-]+)", md):
                self.assertTrue((d / ref).exists(), f"{d.name}/SKILL.md references missing {ref}")
            for py in d.glob("*.py"):
                self.assertIn(py.name, md, f"{d.name}/{py.name} is not mentioned in SKILL.md - dead file?")

    def test_imports_stdlib_only_and_offline(self):
        for py in SKILLS.rglob("*.py"):
            rel = str(py.relative_to(SKILLS))
            mods = set()
            for node in ast.walk(ast.parse(py.read_text(), rel)):
                if isinstance(node, ast.Import):
                    mods |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    mods.add(node.module.split(".")[0])
            extra = mods - set(sys.stdlib_module_names) - MODEL_ADAPTERS.get(rel, set())
            self.assertEqual(extra, set(), f"{rel}: non-stdlib imports (new dependency?)")
            self.assertEqual(mods & NETWORK, set(), f"{rel}: scripts must not talk to the network")

    def test_every_script_has_working_help(self):
        for py in SKILLS.rglob("*.py"):
            rel = str(py.relative_to(SKILLS))
            if rel in MODEL_ADAPTERS:
                continue
            p = run(str(py), "--help")
            self.assertEqual(p.returncode, 0, f"{rel} --help failed:\n{p.stderr}")
            self.assertIn("usage", p.stdout.lower(), rel)


if __name__ == "__main__":
    unittest.main()
