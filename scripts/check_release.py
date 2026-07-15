#!/usr/bin/env python3
"""Dependency-free release audit for Fusion AI Modeler."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "fusion-ai-modeler"
VALIDATOR = SKILL / "scripts" / "validate_model_plan.py"
ADDIN_VALIDATOR = ROOT / "fusion-addin" / "FusionAIModeler" / "tools" / "model_plan_validation.py"
PUBLIC_EXAMPLES = [
    ROOT / "examples" / "generic-sensor-enclosure.modelplan.json",
    ROOT / "examples" / "robotics-control-panel.modelplan.json",
    ROOT / "examples" / "configurable-stepper-mount.modelplan.json",
]


def fail(message: str) -> None:
    raise AssertionError(message)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Invalid JSON at {path.relative_to(ROOT)}: {exc}")


def load_validator():
    spec = importlib.util.spec_from_file_location("release_validator", VALIDATOR)
    if spec is None or spec.loader is None:
        fail("Could not load the ModelPlan validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    required = [
        ROOT / "README.md",
        ROOT / "LICENSE",
        ROOT / "SECURITY.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "THIRD_PARTY_NOTICES.md",
        ROOT / ".gitignore",
        ROOT / ".github" / "workflows" / "tests.yml",
        ROOT / ".codex" / "config.toml",
        ROOT / "examples" / "README.md",
        *PUBLIC_EXAMPLES,
        SKILL / "SKILL.md",
        SKILL / "agents" / "openai.yaml",
        SKILL / "assets" / "model-plan.schema.json",
        ROOT / "fusion-addin" / "FusionAIModeler" / "FusionAIModeler.manifest",
        ROOT / "fusion-addin" / "FusionAIModeler" / "LICENSE.autodesk.txt",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        fail(f"Missing release files: {', '.join(missing)}")

    if VALIDATOR.read_bytes() != ADDIN_VALIDATOR.read_bytes():
        fail("The skill and add-in ModelPlan validators have drifted")

    schema = read_json(SKILL / "assets" / "model-plan.schema.json")
    if schema.get("$id") != "urn:fusion-ai-modeler:schema:model-plan:1.0":
        fail("ModelPlan schema has an unexpected $id")

    example_path = SKILL / "assets" / "model-plan.example.json"
    example = read_json(example_path)
    errors = load_validator().validate_plan(example)
    if errors:
        fail(f"Bundled ModelPlan example is invalid: {errors}")

    public_feature_count = 0
    for public_example_path in PUBLIC_EXAMPLES:
        public_example = read_json(public_example_path)
        errors = load_validator().validate_plan(public_example)
        if errors:
            relative_path = public_example_path.relative_to(ROOT)
            fail(f"Public ModelPlan example is invalid at {relative_path}: {errors}")
        public_feature_count += len(public_example["features"])

    read_json(SKILL / "assets" / "parts-catalog.json")
    manifest = read_json(ROOT / "fusion-addin" / "FusionAIModeler" / "FusionAIModeler.manifest")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(manifest.get("version", ""))):
        fail("Fusion manifest version is not semantic x.y.z")
    if manifest.get("runOnStartup") is not True:
        fail("Fusion manifest must enable runOnStartup")

    config = tomllib.loads((ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"))
    server = config.get("mcp_servers", {}).get("fusion360", {})
    if server.get("url") != "http://127.0.0.1:9100/":
        fail("Codex MCP config must use the loopback Fusion URL")

    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    if not skill_text.startswith("---\nname: fusion-ai-modeler\n"):
        fail("Skill frontmatter name is missing or malformed")
    if "arbitrary code-execution tool" not in skill_text:
        fail("Skill must retain the arbitrary-execution prohibition")

    source_root = ROOT / "fusion-addin" / "FusionAIModeler"
    suspicious = re.compile(r"\b(?:exec|eval|os\.system|subprocess\.Popen)\s*\(")
    for path in source_root.rglob("*.py"):
        if suspicious.search(path.read_text(encoding="utf-8")):
            fail(f"Potential arbitrary execution primitive in {path.relative_to(ROOT)}")

    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    if "AutodeskFusion360/FusionMCPSample" not in notices:
        fail("Autodesk upstream attribution is missing")

    print("Release audit passed")
    print(f"  manifest version: {manifest['version']}")
    print(f"  example features: {len(example['features'])}")
    print(f"  public examples: {len(PUBLIC_EXAMPLES)} plans / {public_feature_count} features")
    print("  validators: identical")
    print("  transport: loopback only")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"Release audit failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
