"""
tests/test_telemetry.py

Unit tests for control/telemetry.py — the shared log_transition utility.
All dependencies are injected so no global singletons are touched.
"""
import unittest
from unittest.mock import MagicMock, patch


class MockEvent:
    def __init__(self, event_type, data):
        self.event_type = event_type
        self.data = data


class TestLogTransition(unittest.TestCase):

    def _make_mocks(self):
        mock_logger = MagicMock()
        mock_logger.worker_id = "worker-test-123"
        mock_bus = MagicMock()
        mock_trail = MagicMock()
        mock_trail.append.return_value = True
        return mock_logger, mock_bus, mock_trail

    def test_publishes_event_to_bus(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log_transition(
            "TASK_CLAIMED", "CLAIMED", "T-001", "oi_labs", "trace-aaa",
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        bus.publish.assert_called_once()
        event_arg = bus.publish.call_args[0][0]
        self.assertIsInstance(event_arg, MockEvent)
        self.assertEqual(event_arg.event_type, "TASK_CLAIMED")

    def test_appends_to_audit_trail(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log_transition(
            "WORKSPACE_PREPARED", "PREPARED", "T-002", "dkffj", "trace-bbb",
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        trail.append.assert_called_once()
        kwargs = trail.append.call_args[1]
        self.assertEqual(kwargs["event_type"], "WORKSPACE_PREPARED")
        self.assertEqual(kwargs["status"], "PREPARED")
        self.assertEqual(kwargs["task_id"], "T-002")
        self.assertEqual(kwargs["project_id"], "dkffj")
        self.assertEqual(kwargs["trace_id"], "trace-bbb")

    def test_passes_optional_fields(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log_transition(
            "GIT_PUSH", "PUSHED", "T-003", "oi_labs", "trace-ccc",
            conversation_id="conv-xyz", branch="task-T-003",
            error_code=None, message="Branch pushed", metadata={"sha": "abc123"},
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        kwargs = trail.append.call_args[1]
        self.assertEqual(kwargs["conversation_id"], "conv-xyz")
        self.assertEqual(kwargs["branch"], "task-T-003")
        self.assertEqual(kwargs["message"], "Branch pushed")
        self.assertEqual(kwargs["metadata"], {"sha": "abc123"})

    def test_uses_injected_worker_id(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log_transition(
            "SESSION_CREATED", "CREATED", "T-004", "oi_labs", "trace-ddd",
            worker_id="custom-worker-id",
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        event_arg = bus.publish.call_args[0][0]
        self.assertEqual(event_arg.data["worker_id"], "custom-worker-id")

    def test_falls_back_to_logger_worker_id(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log.worker_id = "logger-worker-99"
        log_transition(
            "SESSION_REUSED", "REUSED", "T-005", "dkffj", "trace-eee",
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        event_arg = bus.publish.call_args[0][0]
        self.assertEqual(event_arg.data["worker_id"], "logger-worker-99")

    def test_never_crashes_on_bus_failure(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        bus.publish.side_effect = RuntimeError("bus down")
        # Must not raise
        log_transition(
            "TASK_CLAIMED", "CLAIMED", "T-006", "oi_labs", "trace-fff",
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        # Audit trail still called even if bus failed
        trail.append.assert_called_once()

    def test_never_crashes_on_audit_trail_failure(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        trail.append.side_effect = RuntimeError("db down")
        # Must not raise
        log_transition(
            "TASK_CLAIMED", "CLAIMED", "T-007", "oi_labs", "trace-ggg",
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        # Event bus still called even if audit trail failed
        bus.publish.assert_called_once()

    def test_never_crashes_on_logger_failure(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log.worker_id = property(lambda self: 1 / 0)  # broken property
        # Must not raise — outer try/except catches everything
        try:
            log_transition(
                "TASK_CLAIMED", "CLAIMED", "T-008", "oi_labs", "trace-hhh",
                worker_id="safe-id",  # explicit worker_id avoids logger.worker_id
                _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
            )
        except Exception as e:
            self.fail(f"log_transition raised unexpectedly: {e}")

    def test_metadata_defaults_to_empty_dict_in_event(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log_transition(
            "TASK_BLOCKED", "BLOCKED", "T-009", "dkffj", "trace-iii",
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        event_arg = bus.publish.call_args[0][0]
        self.assertEqual(event_arg.data["metadata"], {})

    def test_event_data_contains_all_required_keys(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log_transition(
            "VERIFICATION_PASSED", "PASSED", "T-010", "oi_labs", "trace-jjj",
            conversation_id="conv-1", branch="task-T-010",
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        event_arg = bus.publish.call_args[0][0]
        required_keys = {"trace_id", "worker_id", "task_id", "project_id",
                         "conversation_id", "branch", "status", "error_code",
                         "message", "metadata"}
        self.assertTrue(required_keys.issubset(set(event_arg.data.keys())))

    def test_error_code_passed_through(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log_transition(
            "WORKSPACE_PREPARED", "FAILED", "T-011", "oi_labs", "trace-kkk",
            error_code="CONFIG_002",
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        kwargs = trail.append.call_args[1]
        self.assertEqual(kwargs["error_code"], "CONFIG_002")

    def test_both_bus_and_trail_receive_same_trace_id(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log_transition(
            "TASK_CLAIMED", "CLAIMED", "T-012", "dkffj", "trace-lll",
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        event_arg = bus.publish.call_args[0][0]
        trail_kwargs = trail.append.call_args[1]
        self.assertEqual(event_arg.data["trace_id"], "trace-lll")
        self.assertEqual(trail_kwargs["trace_id"], "trace-lll")

    def test_none_metadata_stays_none_in_trail(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log_transition(
            "GIT_CHECKOUT", "CHECKOUT", "T-013", "oi_labs", "trace-mmm",
            metadata=None,
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        kwargs = trail.append.call_args[1]
        self.assertIsNone(kwargs["metadata"])

    def test_event_type_is_correct_in_bus(self):
        from control.telemetry import log_transition
        log, bus, trail = self._make_mocks()
        log_transition(
            "ANTIGRAVITY_STARTED", "DELEGATED", "T-014", "oi_labs", "trace-nnn",
            _logger=log, _event_bus=bus, _audit_trail=trail, _Event=MockEvent
        )
        event_arg = bus.publish.call_args[0][0]
        self.assertEqual(event_arg.event_type, "ANTIGRAVITY_STARTED")


class TestLogTransitionWithGlobalFallback(unittest.TestCase):
    """Tests that log_transition works with global singletons (no injection)."""

    def test_import_succeeds(self):
        from control.telemetry import log_transition
        self.assertTrue(callable(log_transition))

    def test_does_not_crash_with_globals(self):
        """With real singletons, must not raise (silently handles all errors)."""
        from control.telemetry import log_transition
        try:
            log_transition(
                "TASK_CLAIMED", "TEST", "unit-test-task", "test-project", "trace-unit"
            )
        except Exception as e:
            self.fail(f"log_transition raised with globals: {e}")


if __name__ == "__main__":
    unittest.main()
