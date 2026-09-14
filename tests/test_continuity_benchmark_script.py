import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


class ContinuityExportTests(unittest.TestCase):
    def test_export_is_unsolved_public_input_with_controller_outside(self):
        script = Path(__file__).resolve().parents[1] / "scripts/continuity_benchmark.py"
        spec = importlib.util.spec_from_file_location("continuity_export_script", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "export"
            module.export(target)
            initial = (target / "A/workspace/batch.py").read_bytes()
            for arm in "ABCD":
                workspace = target / arm / "workspace"
                self.assertEqual((workspace / "batch.py").read_bytes(), initial)
                self.assertEqual(set(p.name for p in workspace.iterdir()),
                                 {"TASK.md", "batch.py", "reader.py", "storage.py", "cli.py", "tests"})
                plan = json.loads((target / arm / "controller/plan.json").read_text())
                self.assertEqual(plan["variant"], arm)
                self.assertEqual(plan["real_api_status"], "NOT_RUN")
            with self.assertRaises(FileExistsError):
                module.export(target)
