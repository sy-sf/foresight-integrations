"""Personal knowledge-space selection and mission management.

Claude Code now writes to exactly one personal knowledge space per
authenticated user. The old static/dynamic/directory-mapped bank settings are
intentionally ignored; storage still uses the canonical "default" partition
behind the API boundary.
"""

from .state import read_state, write_state

PERSONAL_KNOWLEDGE_PARTITION = "default"


def derive_bank_id(hook_input: dict, config: dict) -> str:
    """Return the canonical personal knowledge-space partition.

    The plugin used to support static, dynamic, and directory-mapped banks.
    Those settings are intentionally ignored because every authenticated user
    now has exactly one personal knowledge space.
    """
    return PERSONAL_KNOWLEDGE_PARTITION


def ensure_bank_mission(client, bank_id: str, config: dict, debug_fn=None):
    """Set the personal knowledge mission on first use, skip if already set.

    Port of the legacy mission-set tracking in index.js

    Uses a state file to persist whether the personal mission has been set
    across ephemeral hook invocations.
    """
    mission = config.get("personalKnowledgeMission", "")
    if not mission or not mission.strip():
        return

    # Check if we've already set the personal mission for this partition.
    missions_set = read_state("bank_missions.json", {})
    if bank_id in missions_set:
        return

    try:
        retain_mission = config.get("retainMission")
        client.set_personal_knowledge_mission(bank_id, mission, retain_mission=retain_mission, timeout=10)
        missions_set[bank_id] = True
        # Keep the legacy state shape bounded for users upgrading older configs.
        if len(missions_set) > 10000:
            keys = sorted(missions_set.keys())
            for k in keys[: len(keys) // 2]:
                del missions_set[k]
        write_state("bank_missions.json", missions_set)
        if debug_fn:
            debug_fn(f"Set personal knowledge mission for partition: {bank_id}")
    except Exception as e:
        # Don't fail if mission set fails; the personal space will be ensured
        # by the API resolver on first retain.
        if debug_fn:
            debug_fn(f"Could not set personal knowledge mission for {bank_id}: {e}")
