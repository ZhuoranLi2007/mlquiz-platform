"""Public skill API surface. The framework lives in services/skill_runtime.py;
the static registry and implementations live next to this package so a skill
bundle is: SKILL.md + schema + real execute function + prompts + examples."""
from backend.app.services.skill_runtime import (  # noqa: F401
    SkillSpec,
    envelope,
    run_skill,
)

__all__ = ["SkillSpec", "envelope", "run_skill"]
