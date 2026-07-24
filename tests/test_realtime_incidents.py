import json
import tempfile
import unittest
from pathlib import Path

from scripts import realtime_incidents as monitor


def incident_payload(status="investigating", impact="major", updates=None):
    return {
        "incidents": [
            {
                "id": "incident-1",
                "name": "Elevated error rates",
                "status": status,
                "impact": impact,
                "shortlink": "https://status.example/incidents/incident-1",
                "components": [{"name": "API"}, {"name": "ChatGPT"}],
                "incident_updates": updates
                or [
                    {
                        "id": "update-1",
                        "status": status,
                        "body": "We are investigating elevated errors.",
                        "created_at": "2026-07-24T09:00:00Z",
                    }
                ],
            }
        ]
    }


class RealtimeIncidentTests(unittest.TestCase):
    def setUp(self):
        self.provider = {
            "name": "OpenAI",
            "api": "https://status.example/api/v2/incidents.json",
            "page": "https://status.example",
        }

    def test_new_active_incident_sends_latest_update_only(self):
        payload = incident_payload(
            updates=[
                {"id": "u1", "status": "investigating", "body": "First", "created_at": "1"},
                {"id": "u2", "status": "identified", "body": "Latest", "created_at": "2"},
            ]
        )
        state = {"version": 1, "providers": {}}
        alerts = monitor.collect_alerts(self.provider, payload, state)
        self.assertEqual([alert.update_id for alert in alerts], ["u2"])

    def test_existing_incident_sends_all_unseen_updates(self):
        state = {
            "version": 1,
            "providers": {"OpenAI": {"incident-1": {"seen_update_ids": ["u1"]}}},
        }
        payload = incident_payload(
            updates=[
                {"id": "u1", "status": "investigating", "body": "First", "created_at": "1"},
                {"id": "u2", "status": "identified", "body": "Second", "created_at": "2"},
                {"id": "u3", "status": "monitoring", "body": "Third", "created_at": "3"},
            ]
        )
        alerts = monitor.collect_alerts(self.provider, payload, state)
        self.assertEqual([alert.update_id for alert in alerts], ["u2", "u3"])

    def test_old_resolved_incident_is_not_replayed(self):
        state = {"version": 1, "providers": {}}
        alerts = monitor.collect_alerts(
            self.provider,
            incident_payload(status="resolved"),
            state,
        )
        self.assertEqual(alerts, [])

    def test_tracked_resolution_is_sent(self):
        state = {
            "version": 1,
            "providers": {"OpenAI": {"incident-1": {"seen_update_ids": ["update-old"]}}},
        }
        alerts = monitor.collect_alerts(
            self.provider,
            incident_payload(status="resolved"),
            state,
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].incident_status, "resolved")

    def test_minor_non_outage_is_excluded(self):
        payload = incident_payload(impact="minor")
        payload["incidents"][0]["name"] = "Small documentation issue"
        state = {"version": 1, "providers": {}}
        self.assertEqual(monitor.collect_alerts(self.provider, payload, state), [])

    def test_feishu_payload_has_security_keyword_and_pm_action(self):
        state = {"version": 1, "providers": {}}
        alert = monitor.collect_alerts(self.provider, incident_payload(), state)[0]
        serialized = json.dumps(monitor.build_feishu_payload(alert), ensure_ascii=False)
        self.assertIn("AI前沿日报", serialized)
        self.assertIn("S级实时进展", serialized)
        self.assertIn("对 AI 产品的影响", serialized)
        self.assertIn("https://status.example/incidents/incident-1", serialized)

    def test_state_round_trip(self):
        state = {"version": 1, "providers": {}}
        alert = monitor.collect_alerts(self.provider, incident_payload(), state)[0]
        monitor.mark_sent(state, alert)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            monitor.save_state(state, path)
            restored = monitor.load_state(path)
        seen = restored["providers"]["OpenAI"]["incident-1"]["seen_update_ids"]
        self.assertEqual(seen, ["update-1"])


if __name__ == "__main__":
    unittest.main()
