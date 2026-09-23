import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


PIPELINE_PATH = Path(__file__).resolve().parents[1] / "scripts/pipeline.py"
SPEC = importlib.util.spec_from_file_location("pipeline", PIPELINE_PATH)
assert SPEC and SPEC.loader
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


def requirement(
    text: str,
    status: str,
    *,
    fact_ids: list[str] | None = None,
) -> dict:
    return {
        "requirement": text,
        "importance": "must",
        "status": status,
        "fact_ids": fact_ids or [],
        "reasoning": "",
    }


class SelectMissingSkillsTests(unittest.TestCase):
    def test_collects_missing_skills_for_score_based_skip(self) -> None:
        vacancy = {"must_have": ["Deep AWS knowledge.", "Terraform experience."]}
        avg = {
            "requirements": [
                requirement("Deep AWS knowledge.", "missing"),
                requirement("Terraform experience.", "unknown"),
            ]
        }
        decision = {"initial_category": "skip", "hard_blockers": []}

        self.assertEqual(
            pipeline.select_missing_skills(vacancy, avg, {}, decision),
            ["Deep AWS knowledge.", "Terraform experience."],
        )

    def test_collects_technical_gap_from_hard_blocked_vacancy(self) -> None:
        vacancy = {"must_have": ["Terraform experience."]}
        avg = {"requirements": [requirement("Terraform experience.", "missing")]}
        decision = {
            "initial_category": "skip",
            "hard_blockers": ["Work authorization is unavailable."],
        }

        self.assertEqual(
            pipeline.select_missing_skills(vacancy, avg, {}, decision),
            ["Terraform experience."],
        )

    def test_collects_single_missing_skill_from_otherwise_complete_match(self) -> None:
        vacancy = {"must_have": ["Python.", "Terraform."]}
        avg = {
            "requirements": [
                requirement("Python.", "confirmed", fact_ids=["skills.python"]),
                requirement("Terraform.", "missing"),
            ]
        }
        decision = {"initial_category": "need-review", "hard_blockers": []}

        self.assertEqual(
            pipeline.select_missing_skills(vacancy, avg, {}, decision),
            ["Terraform."],
        )

    def test_collects_missing_skill_found_only_by_red_team(self) -> None:
        vacancy = {"must_have": ["Python.", "Terraform."]}
        avg = {
            "requirements": [
                requirement("Python.", "confirmed", fact_ids=["skills.python"]),
                requirement("Terraform.", "partial"),
            ]
        }
        red = {
            "requirements": [
                requirement("Python.", "confirmed", fact_ids=["skills.python"]),
                requirement("Terraform.", "missing"),
            ]
        }
        decision = {"initial_category": "skip", "hard_blockers": []}

        self.assertEqual(
            pipeline.select_missing_skills(vacancy, avg, red, decision),
            ["Terraform."],
        )

    def test_collects_up_to_eight_missing_skills(self) -> None:
        skills = [
            "AWS.",
            "Terraform.",
            "Helm.",
            "Go.",
            "Argo CD.",
            "Vault.",
            "Crossplane.",
            "Pulumi.",
        ]
        vacancy = {"must_have": skills}
        avg = {
            "requirements": [requirement(skill, "missing") for skill in skills]
        }
        decision = {"initial_category": "skip", "hard_blockers": []}

        self.assertEqual(
            pipeline.select_missing_skills(vacancy, avg, {}, decision),
            skills,
        )

    def test_discards_the_ninth_missing_skill(self) -> None:
        skills = [
            "AWS.",
            "Terraform.",
            "Helm.",
            "Go.",
            "Argo CD.",
            "Vault.",
            "Crossplane.",
            "Pulumi.",
            "Nomad.",
        ]
        vacancy = {"must_have": skills}
        avg = {
            "requirements": [requirement(skill, "missing") for skill in skills]
        }
        decision = {"initial_category": "skip", "hard_blockers": []}

        self.assertEqual(
            pipeline.select_missing_skills(vacancy, avg, {}, decision),
            skills[:8],
        )

    def test_matches_paraphrased_requirements_by_tokens(self) -> None:
        vacancy = {
            "must_have": [
                "Production infrastructure as code using Terraform.",
            ]
        }
        avg = {
            "requirements": [
                requirement("Terraform IaC in production.", "missing"),
            ]
        }
        decision = {"initial_category": "need-review", "hard_blockers": []}

        self.assertEqual(
            pipeline.select_missing_skills(vacancy, avg, {}, decision),
            ["Production infrastructure as code using Terraform."],
        )

    def test_other_evaluator_fact_id_cancels_paraphrased_missing_signal(self) -> None:
        vacancy = {"must_have": ["Infrastructure as code with Terraform."]}
        avg = {
            "requirements": [
                requirement("Terraform IaC experience.", "missing"),
            ]
        }
        red = {
            "requirements": [
                requirement(
                    "Infrastructure as code with Terraform.",
                    "partial",
                    fact_ids=["skills.terraform"],
                ),
            ]
        }
        decision = {"initial_category": "need-review", "hard_blockers": []}

        self.assertEqual(
            pipeline.select_missing_skills(vacancy, avg, red, decision),
            [],
        )

    def test_excludes_hiring_filters_from_missing_skills(self) -> None:
        filters = [
            "Must be located in Germany.",
            "EU work authorization is required.",
            "Fluent German language.",
            "EU citizenship.",
            "Fixed schedule during CET business hours.",
            "40 hours per week.",
            "Security clearance is required.",
        ]
        vacancy = {"must_have": [*filters, "Terraform experience."]}
        avg = {
            "requirements": [
                *[requirement(item, "missing") for item in filters],
                requirement("Terraform experience.", "missing"),
            ]
        }
        decision = {
            "initial_category": "skip",
            "hard_blockers": filters,
        }

        self.assertEqual(
            pipeline.select_missing_skills(vacancy, avg, {}, decision),
            ["Terraform experience."],
        )

    def test_does_not_expand_skill_to_tenure_requirement(self) -> None:
        vacancy = {
            "must_have": [
                "3+ years of production Kubernetes and AWS experience.",
            ]
        }
        avg = {
            "requirements": [
                requirement("Production Kubernetes and AWS.", "missing"),
            ]
        }
        decision = {"initial_category": "skip", "hard_blockers": []}

        self.assertEqual(
            pipeline.select_missing_skills(vacancy, avg, {}, decision),
            ["Production Kubernetes and AWS."],
        )

    def test_missing_requirement_with_evidence_is_not_absent_from_master_cv(self) -> None:
        vacancy = {"must_have": ["Eight years of Python engineering."]}
        avg = {
            "requirements": [
                requirement(
                    "Eight years of Python engineering.",
                    "missing",
                    fact_ids=["career.technical-experience-since"],
                )
            ]
        }
        decision = {"initial_category": "skip", "hard_blockers": []}

        self.assertEqual(
            pipeline.select_missing_skills(vacancy, avg, {}, decision),
            [],
        )


class RecordMissingSkillsTests(unittest.TestCase):
    def test_connect_db_creates_missing_skills_table(self) -> None:
        temp_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        original_db_path = pipeline.DB_PATH
        pipeline.DB_PATH = temp_dir / "jobs.sqlite"
        self.addCleanup(setattr, pipeline, "DB_PATH", original_db_path)

        with pipeline.connect_db() as connection:
            columns = connection.execute(
                "PRAGMA table_info(missing_skills)"
            ).fetchall()

        self.assertEqual(
            [column["name"] for column in columns],
            [
                "job_id",
                "company",
                "title",
                "source_url",
                "skill",
                "recorded_at",
            ],
        )

    def test_writes_each_skill_once(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE missing_skills (
                job_id TEXT NOT NULL,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                skill TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (job_id, skill)
            )
            """
        )
        job_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pipeline.write_json(
            job_dir / "metadata.json",
            {
                "job_id": "example-platform-engineer-12345678",
                "company": "Example",
                "title": "Platform Engineer",
                "source_url": "https://example.com/job",
            },
        )
        pipeline.write_json(
            job_dir / "vacancy.json",
            {"must_have": ["Terraform."]},
        )
        avg = {"requirements": [requirement("Terraform.", "missing")]}
        decision = {"initial_category": "skip", "hard_blockers": []}

        pipeline.record_missing_skills(connection, job_dir, avg, {}, decision)
        pipeline.record_missing_skills(connection, job_dir, avg, {}, decision)

        rows = connection.execute(
            "SELECT job_id, skill FROM missing_skills"
        ).fetchall()
        self.assertEqual(
            rows,
            [("example-platform-engineer-12345678", "Terraform.")],
        )


class MergeDecisionTests(unittest.TestCase):
    def make_job_dir(self) -> Path:
        job_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pipeline.write_json(job_dir / "metadata.json", {"job_id": "example-job"})
        return job_dir

    @staticmethod
    def report(score: int, blockers: list[str]) -> dict:
        return {
            "score": score,
            "hard_blockers": blockers,
            "recommended_positioning": "Use verified platform evidence.",
        }

    def test_timezone_only_blocker_does_not_force_skip(self) -> None:
        blocker = (
            "Remote scope is GMT-7 to GMT+4, while the candidate is in Almaty."
        )

        decision = pipeline.merge_decision(
            self.make_job_dir(),
            self.report(82, [blocker]),
            self.report(68, [blocker]),
        )

        self.assertEqual(decision["initial_category"], "apply")
        self.assertEqual(decision["hard_blockers"], [])
        self.assertEqual(decision["ignored_timezone_blockers"], [blocker])

    def test_real_blocker_still_forces_skip_alongside_timezone(self) -> None:
        timezone_blocker = "The advertised remote timezone is UTC-5 to UTC+2."
        visa_blocker = "Required US work authorization is unavailable."

        decision = pipeline.merge_decision(
            self.make_job_dir(),
            self.report(82, [timezone_blocker]),
            self.report(68, [visa_blocker]),
        )

        self.assertEqual(decision["initial_category"], "skip")
        self.assertEqual(decision["hard_blockers"], [visa_blocker])
        self.assertEqual(
            decision["ignored_timezone_blockers"],
            [timezone_blocker],
        )

    def test_explicit_timezone_schedule_remains_a_blocker(self) -> None:
        blocker = "The role requires fixed working hours in the CET timezone."

        decision = pipeline.merge_decision(
            self.make_job_dir(),
            self.report(82, [blocker]),
            self.report(68, []),
        )

        self.assertEqual(decision["initial_category"], "skip")
        self.assertEqual(decision["hard_blockers"], [blocker])
        self.assertEqual(decision["ignored_timezone_blockers"], [])

    def test_low_scores_without_blockers_still_skip(self) -> None:
        decision = pipeline.merge_decision(
            self.make_job_dir(),
            self.report(58, []),
            self.report(44, []),
        )

        self.assertEqual(decision["initial_category"], "skip")
        self.assertEqual(decision["ignored_timezone_blockers"], [])


if __name__ == "__main__":
    unittest.main()
