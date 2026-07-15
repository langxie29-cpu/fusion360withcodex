# Contributing

Contributions are welcome, especially typed ModelPlan features, validator improvements,
cross-platform installation fixes, localization, and reproducible mechanical examples.

## Design constraints

- Do not add a general Python, shell, or Fusion script execution tool.
- Keep every write operation representable in the versioned ModelPlan schema.
- Validate plans before staging and stage them before applying.
- Default to a new Fusion document.
- Keep purchased-part dimensions explicitly sourced or labelled as unverified candidates.
- Preserve localhost-only transport and browser cross-origin protections.

## Making a change

1. Update both validators when ModelPlan syntax changes:
   - `.agents/skills/fusion-ai-modeler/scripts/validate_model_plan.py`
   - `fusion-addin/FusionAIModeler/tools/model_plan_validation.py`
2. Update `model-plan.schema.json` and `references/model-plan.md`.
3. Add unit tests for valid and invalid plans.
4. Run:

   ```text
   python -m unittest discover -s tests -v
   python scripts/check_release.py
   python -m compileall -q fusion-addin/FusionAIModeler .agents/skills/fusion-ai-modeler/scripts tests
   ```

5. Smoke-test Fusion-facing changes inside Fusion and include the observed views and version
   in the pull request description.

Keep pull requests focused and describe any inferred geometry or compatibility assumptions.
