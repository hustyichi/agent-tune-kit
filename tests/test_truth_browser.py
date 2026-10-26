import json
import re
import shutil
import subprocess
from pathlib import Path

from agent_tune_kit.visualize.dataset import generate_dataset
from tests.test_truth_contract import dataset


def test_browser_exposes_bound_reviews_for_each_answer_field(tmp_path):
    path = dataset(tmp_path)
    generate_dataset(path, open_browser=False)
    html = path.with_name("dataset.html").read_text()
    payload = json.loads(re.search(r'<script[^>]*id="dataset-data"[^>]*>(.*?)</script>', html, re.S).group(1))
    assert payload["reviewBinding"]["dataset_sha256"]
    assert payload["rows"][0]["truth"]["expected"]["status"] == "unreviewed"
    assert payload["rows"][0]["truth"]["label"]["status"] == "unreviewed"


def test_review_export_binds_selected_field_and_does_not_count_feedback_as_confirmation(tmp_path):
    runtime = shutil.which("bun") or shutil.which("node")
    assert runtime, "JavaScript runtime required for review behavior test"
    js = (Path(__file__).parents[1] / "agent_tune_kit/visualize/assets/dataset/app.js").read_text()
    payload = dict(
        datasetPath="dataset.csv",
        fieldnames=["atk_id", "input", "expected", "label"],
        roles={"input": {"field": "input"}, "expected": {"field": "expected"}},
        reviewBinding={"dataset_sha256": "hash", "dataset_scope": "scope", "standard": {"sha256": "standard"}},
        rows=[
            {
                "atkId": "1",
                "rowNumber": 1,
                "values": {"input": "x", "expected": "a", "label": "b"},
                "truth": {"expected": {"status": "unreviewed"}, "label": {"status": "unreviewed"}},
            }
        ],
    )
    harness = "global.window={localStorage:{getItem(){return null},setItem(){}}};\n"
    harness += f"const payload={json.dumps(payload)};\n"
    harness += "global.document={getElementById(){return {textContent:JSON.stringify(payload)}}};\n"
    harness += js.replace(
        "  init();\n})();",
        """
      setFeedback(payload.rows[0], "fix this");
      const first=reviewCounts();
      setReviewField(payload.rows[0], "review_status", "confirmed");
      setReviewField(payload.rows[0], "source", "policy");
      setReviewField(payload.rows[0], "evidence", "supported");
      setReviewField(payload.rows[0], "reviewer", "owner");
      const exported=reviewExportRows();
      roles.expected={field:"label"};
      console.log(JSON.stringify({first,exported,other:getFeedback(payload.rows[0])}));
    })();""",
    )
    run = subprocess.run([runtime, "-e", harness], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    output = json.loads(run.stdout)
    assert output["first"]["reviewed"] == 0
    assert output["exported"][0]["field"] == "expected"
    assert output["exported"][0]["dataset_sha256"] == "hash"
    assert output["exported"][0]["review_status"] == "confirmed"
    assert output["other"] == ""
