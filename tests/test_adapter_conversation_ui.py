"""Keep native conversation UI wired to the Core lifecycle contracts."""

from pathlib import Path

ADAPTER = Path(__file__).resolve().parents[1] / "adapter/LivingNPC.GameAdapter"


def source(name):
    return (ADAPTER / name).read_text(encoding="utf-8-sig")


def test_text_controls_are_suppressed_before_tick_early_returns():
    script = source("LivingNpcScript.cs")
    tick = script.split("private void OnTick", 1)[1].split(
        "private void PruneAndPublish", 1
    )[0]
    assert "inputControls.DisableForFrame(typing != null, game.DisableControl)" in tick
    assert tick.index("inputControls.DisableForFrame") < tick.index("return;")
    assert "Hash.DISABLE_CONTROL_ACTION, group, control, true" in source(
        "GtaGameAdapter.cs"
    )


def test_enter_checks_phone_before_discarding_the_draft():
    script = source("LivingNpcScript.cs")
    submit = script.split("if (args.KeyCode == Keys.Enter)", 1)[1].split(
        "if (args.KeyCode == Keys.Back", 1
    )[0]
    assert "game.PhoneActive" in submit
    assert submit.index("game.PhoneActive") < submit.index("typing = null")
    assert "inputControls.KeyDown((int)args.KeyCode, typing != null)" in script
    assert "inputControls.KeyUp((int)args.KeyCode)" in script


def test_status_expires_and_new_turn_replaces_cancellation():
    script = source("LivingNpcScript.cs")
    game = source("GtaGameAdapter.cs")
    assert 'game.ShowStatus("typing",' in script
    assert 'game.ShowStatus("thinking",' in script
    assert 'ShowStatus("talking",' in game
    assert "status.Read(DateTimeOffset.UtcNow)" in game


def test_selected_target_is_drawn_each_render_on_the_script_thread():
    game = source("GtaGameAdapter.cs")
    render = game.split("public void Render(", 1)[1]
    assert render.index("OnScriptThread();") < render.index("RenderTarget()")
    target = game.split("private string? RenderTarget()", 1)[1].split(
        "public void Render(", 1
    )[0]
    assert target.index("Resolve(SelectedActorId)") < target.index("World.DrawMarker(")
    assert "TargetPresentation.Label(" in target
    assert "Bone.SkelHead" in target


def test_timeline_retry_uses_configured_key_and_disposes_the_refused_bridge():
    script = source("LivingNpcScript.cs")
    assert "confirmProfileKey: confirmProfileKey.ToString()" in script
    confirm = script.split("if (args.KeyCode == confirmProfileKey)", 1)[1].split(
        "if (!profileConfirmation.Confirmed) return;", 1
    )[0]
    assert confirm.index("if (!profileKeyHeld)") < confirm.index(
        "bridge?.TimelineSelectionRequired == true"
    )
    retry = confirm.split("bridge?.TimelineSelectionRequired == true", 1)[1].split(
        "else if", 1
    )[0]
    for operation in (
        "CancelInput()",
        "bridge.Dispose()",
        "bridge = null",
        "controller = null",
        "game.Reset()",
    ):
        assert operation in retry
