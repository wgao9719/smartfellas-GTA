"""Keep the unsafe post-startup decorator path out of shipped binding code."""

from pathlib import Path


def test_adapter_binding_does_not_use_decorators_or_memory_writes():
    adapter = Path(__file__).resolve().parents[1] / "adapter"
    forbidden = (
        "DECOR_",
        "DecoratorInterface",
        "DecoratorRegistration",
        "runtime_decorator_binding",
        "Marshal.Write",
        "WriteProcessMemory",
    )
    violations = []
    for project in ("LivingNPC.Core", "LivingNPC.GameAdapter"):
        for path in (adapter / project).glob("*.cs"):
            source = path.read_text(encoding="utf-8-sig")
            violations.extend(
                f"{path.name}: {token}" for token in forbidden if token in source
            )
    assert not violations, violations
