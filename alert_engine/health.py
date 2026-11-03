"""One aggregate write per full engine run; no user data in public heartbeat."""
import os
from datetime import datetime, timezone


def write_engine_health(store, started, status_counts, channel_counts, evaluation_errors, delivery_errors):
    finished = datetime.now(timezone.utc).isoformat()
    updates = {
        "lastRunStartedAt": started.isoformat(), "lastRunFinishedAt": finished,
        "evaluationErrors": evaluation_errors, "deliveryErrors": delivery_errors,
        "statusCounts": dict(status_counts), "channelCounts": dict(channel_counts),
        "runId": os.environ.get("GITHUB_RUN_ID", "local"),
        "version": "recovery-v1",
    }
    if evaluation_errors == 0:
        updates["lastEvaluationSuccessAt"] = finished
    if evaluation_errors + delivery_errors == 0:
        updates["lastSuccessfulRunAt"] = finished
    for channel in ("push", "telegram"):
        if channel_counts.get(f"{channel}_sent", 0):
            updates[f"last{channel.title()}AcceptedAt"] = finished
    store.get_db().collection("systemHealth").document("alertEngine").set(updates, merge=True)
