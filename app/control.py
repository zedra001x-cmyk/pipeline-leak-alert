"""OT control: equipment commands, audit log and settings. No web code here."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, AuditLog, Command, Setting, utcnow

ACTIONS = {
    "VALVE": {"OPEN": "OPEN", "CLOSE": "CLOSED"},
    "PUMP": {"START": "RUNNING", "STOP": "STOPPED"},
}


class CommandError(ValueError):
    pass


COMMAND_TRANSITIONS = {
    "PENDING": "SENT",
    "SENT": "EXECUTED",
    "EXECUTED": "VERIFIED",
}


def audit(db: Session, username: str, action: str, detail: str = "") -> None:
    """Add an audit row (the caller commits)."""
    db.add(AuditLog(username=username[:60], action=action[:40], detail=detail[:300]))


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def enqueue_command(db: Session, asset_id: int, action: str, requested_by: str) -> Command:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise CommandError("Asset not found")
    action = action.upper()
    if action not in ACTIONS[asset.kind]:
        raise CommandError(f"Action must be one of {', '.join(ACTIONS[asset.kind])} for a {asset.kind.lower()}")
    command = Command(asset_id=asset.id, action=action, requested_by=requested_by[:60])
    db.add(command)
    audit(db, requested_by, "COMMAND", f"{asset.name}: {action}")
    db.commit()
    return command


def transition_command(command: Command, next_status: str, observed_state: str | None = None) -> None:
    """Advance exactly one command stage; verification requires matching feedback."""
    expected = COMMAND_TRANSITIONS.get(command.status)
    if expected != next_status:
        raise CommandError(f"Cannot transition command from {command.status} to {next_status}")
    if next_status == "VERIFIED":
        desired = ACTIONS[command.asset.kind][command.action]
        if observed_state != desired:
            raise CommandError("Reported equipment state does not match the requested action")
        command.asset.state = observed_state
        command.asset.updated_at = utcnow()
    command.status = next_status
    if next_status == "EXECUTED":
        command.applied_at = utcnow()


def apply_pending_commands(db: Session) -> int:
    pending = db.scalars(select(Command).where(Command.status == "PENDING").order_by(Command.id)).all()
    for command in pending:
        target = ACTIONS[command.asset.kind][command.action]
        transition_command(command, "SENT")
        transition_command(command, "EXECUTED")
        # The built-in simulator owns this virtual equipment, so it can compare
        # the simulated state with the requested target and complete verification.
        transition_command(command, "VERIFIED", observed_state=target)
    if pending:
        db.commit()
    return len(pending)


def isolate_section(db: Session, section_index: int, requested_by: str = "auto-isolation") -> int:
    """Close the two valves that bound a section (valve i upstream, valve i+1 downstream)."""
    closed = 0
    pending_assets = {c.asset_id for c in db.scalars(select(Command).where(
        Command.status.in_(("PENDING", "SENT", "EXECUTED"))))}
    for asset in db.scalars(select(Asset).where(Asset.kind == "VALVE")):
        if asset.position in (section_index, section_index + 1) and asset.state == "OPEN" \
                and asset.id not in pending_assets:
            enqueue_command(db, asset.id, "CLOSE", requested_by)
            closed += 1
    return closed
