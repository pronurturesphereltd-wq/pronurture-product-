"""Tests for FCM delivery, focused on which failures kill a device row.

Getting this wrong is expensive in both directions: too narrow and dead tokens
are retried on every publish forever, too broad and a payload bug silently
deletes every registered handset.
"""

from django.test import SimpleTestCase

from core.push import _is_dead_token


class FakeUnregisteredError(Exception):
    pass


FakeUnregisteredError.__name__ = "UnregisteredError"


class FakeSenderIdMismatchError(Exception):
    pass


FakeSenderIdMismatchError.__name__ = "SenderIdMismatchError"


class FakeInvalidArgumentError(Exception):
    pass


FakeInvalidArgumentError.__name__ = "InvalidArgumentError"


class DeadTokenTests(SimpleTestCase):
    def test_unregistered_is_dead(self):
        self.assertTrue(
            _is_dead_token(FakeUnregisteredError("Requested entity was not found."))
        )

    def test_sender_id_mismatch_is_dead(self):
        self.assertTrue(
            _is_dead_token(FakeSenderIdMismatchError("SenderId mismatch"))
        )

    def test_invalid_registration_token_is_dead(self):
        """The exact message FCM returned during live verification."""
        self.assertTrue(
            _is_dead_token(
                FakeInvalidArgumentError(
                    "The registration token is not a valid FCM registration token"
                )
            )
        )

    def test_invalid_payload_is_not_a_dead_token(self):
        """The regression that matters: a malformed payload is our bug, not a
        dead handset. Pruning here would wipe every device in the database."""
        self.assertFalse(
            _is_dead_token(
                FakeInvalidArgumentError(
                    "Invalid value at 'message.notification.title' (TYPE_STRING)"
                )
            )
        )

    def test_transient_server_error_is_not_a_dead_token(self):
        self.assertFalse(_is_dead_token(Exception("503 Service Unavailable")))

    def test_auth_failure_is_not_a_dead_token(self):
        """A clock-skew invalid_grant took down every send during Phase 1A
        verification. It must never be read as the devices being dead."""
        self.assertFalse(
            _is_dead_token(
                Exception("invalid_grant: Invalid JWT: Token must be short-lived")
            )
        )
