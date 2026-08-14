import unittest

from bson import ObjectId

from app.core.proposals import latest_proposal, payment_eligibility


class ProposalPaymentEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.client_id = ObjectId()
        self.editor_id = ObjectId()
        self.request_id = ObjectId()

    def request(self, history=None, version=0, **fields):
        return {
            "_id": self.request_id,
            "user_id": self.client_id,
            "editor_user_id": self.editor_id,
            "proposal_version": version,
            "proposal_history": history or [],
            **fields,
        }

    def proposal(self, version=1, *, amount=5000, days=3, client=False, editor=False):
        return {
            "_id": ObjectId(),
            "version": version,
            "amount": amount,
            "delivery_days": days,
            "message": "Final project terms",
            "created_by": self.editor_id,
            "client_accepted": client,
            "editor_accepted": editor,
        }

    def test_no_proposal_is_blocked(self):
        result = payment_eligibility(self.request())
        self.assertFalse(result["payment_allowed"])
        self.assertEqual(result["message"], "No proposal has been submitted.")

    def test_original_stored_offer_remains_payable_for_legacy_accepted_request(self):
        result = payment_eligibility(self.request(
            status="accepted", proposal_required=None,
            proposal_amount=1000, proposal_delivery_days=27,
            proposal_submitted_at="legacy-timestamp",
        ))
        self.assertTrue(result["payment_allowed"])
        self.assertEqual(result["amount"], 1000)
        self.assertEqual(result["delivery_days"], 27)

    def test_budget_or_modern_unstarted_request_is_never_treated_as_price(self):
        result = payment_eligibility(self.request(
            status="accepted", proposal_required=True,
            proposal_status="not_started", brief={"budget_max": 5000},
        ))
        self.assertFalse(result["payment_allowed"])
        self.assertIsNone(result["amount"])

    def test_nobody_accepted_is_blocked(self):
        result = payment_eligibility(self.request([self.proposal()], version=1))
        self.assertFalse(result["payment_allowed"])
        self.assertEqual(result["message"], "Client must accept the latest proposal.")

    def test_only_client_accepted_is_blocked_for_editor(self):
        result = payment_eligibility(self.request([self.proposal(client=True)], version=1))
        self.assertFalse(result["payment_allowed"])
        self.assertEqual(result["message"], "Editor must accept the latest proposal.")

    def test_only_editor_accepted_is_blocked_for_client(self):
        result = payment_eligibility(self.request([self.proposal(editor=True)], version=1))
        self.assertFalse(result["payment_allowed"])
        self.assertEqual(result["message"], "Client must accept the latest proposal.")

    def test_both_accepted_uses_server_proposal_terms(self):
        result = payment_eligibility(self.request([self.proposal(client=True, editor=True)], version=1))
        self.assertTrue(result["payment_allowed"])
        self.assertEqual(result["amount"], 5000)
        self.assertEqual(result["delivery_days"], 3)
        self.assertEqual(result["message"], "Proposal accepted by both parties. Payment is ready.")

    def test_new_revision_resets_old_acceptance(self):
        history = [
            self.proposal(version=1, client=True, editor=True),
            self.proposal(version=2, amount=6000, client=False, editor=True),
        ]
        result = payment_eligibility(self.request(history, version=2))
        self.assertFalse(result["payment_allowed"])
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["amount"], 6000)
        self.assertEqual(result["message"], "Client must accept the latest proposal.")

    def test_legacy_accepted_proposal_is_derived_from_real_actors(self):
        proposal = self.proposal()
        proposal.pop("client_accepted")
        proposal.pop("editor_accepted")
        doc = self.request(
            [proposal], version=1, proposal_status="accepted",
            proposal_accepted_by=self.client_id,
        )
        latest = latest_proposal(doc)
        self.assertTrue(latest["client_accepted"])
        self.assertTrue(latest["editor_accepted"])


if __name__ == "__main__":
    unittest.main()
