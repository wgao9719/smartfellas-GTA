"""Administrative export of one character's retained history across branches."""

import json


def export_actor(repository, profile_id, actor_id):
    actor = repository.get_actor(profile_id, actor_id)
    if actor is None:
        raise ValueError("unknown actor in this profile")
    profile = repository.export_profile(profile_id)
    exported = {key: profile[key] for key in (
        "schema_version", "memories", "knowledge_cards", "relationships", "skill_records",
        "skill_sources", "skill_versions", "events", "branches",
    )}
    exported.update(
        scope="administrative_actor_all_branches",
        actor_id=actor_id,
        profile={"profile_id": profile_id},
        actors=[actor],
    )
    for key in ("memories", "relationships", "skill_records"):
        exported[key] = [row for row in exported[key] if row["actor_id"] == actor_id]
    exported["knowledge_cards"] = [
        row for row in exported["knowledge_cards"] if row["owner_id"] == actor_id
    ]
    record_ids = {row["record_id"] for row in exported["skill_records"]}
    exported["skill_sources"] = [
        row for row in exported["skill_sources"] if row["record_id"] in record_ids
    ]
    versions = {(row["skill_id"], row["version"]) for row in exported["skill_records"]}
    exported["skill_versions"] = [
        row for row in exported["skill_versions"] if (row["skill_id"], row["version"]) in versions
    ]
    sources = {
        event_id
        for key in ("memories", "knowledge_cards")
        for row in exported[key]
        for event_id in row["source_event_ids"]
    }
    sources.update(row["event_id"] for row in exported["relationships"])
    sources.update(row["event_id"] for row in exported["skill_sources"])
    forgotten = {
        value[1]
        for row in repository.connection.execute(
            "SELECT object_id FROM scrub_audit WHERE profile_id=? "
            "AND operation='forget_actor_source'",
            (profile_id,),
        )
        if (value := json.loads(row[0]))[0] == actor_id
    }
    exported["events"] = [
        event for event in exported["events"]
        if event["event_id"] not in forgotten
        and (event.get("payload", {}).get("visibility_scope") != "actor"
             or event["payload"].get("scope_id") == actor_id)
        and (event["event_id"] in sources or actor_id in event.get(
            "observer_ids", event.get("payload", {}).get("observer_ids", []),
        ))
    ]
    branch_ids = {
        row["branch_id"]
        for key in ("memories", "knowledge_cards", "relationships", "skill_records")
        for row in exported[key]
    }
    event_ids = {event["event_id"] for event in exported["events"]}
    branch_ids.update(
        row["branch_id"] for row in repository.connection.execute(
            "SELECT event_id,branch_id FROM events WHERE profile_id=?", (profile_id,),
        ) if row["event_id"] in event_ids
    )
    exported["branches"] = [
        branch for branch in exported["branches"] if branch["branch_id"] in branch_ids
    ]
    return exported
