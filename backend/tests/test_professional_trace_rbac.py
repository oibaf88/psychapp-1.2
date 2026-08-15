import unittest
import uuid
from types import SimpleNamespace

from fastapi import HTTPException

from app.routers.professional import _require_clinical_read


class _AssignmentQuery:
    def __init__(self, assignment):
        self.assignment = assignment

    def filter(self, *_args):
        return self

    def first(self):
        return self.assignment


class _Db:
    def __init__(self, assignment=None):
        self.assignment = assignment

    def query(self, _model):
        return _AssignmentQuery(self.assignment)


class ProfessionalTraceRbacTests(unittest.TestCase):
    def test_supervisor_can_read_clinical_trace(self):
        _require_clinical_read(
            _Db(),
            SimpleNamespace(id=uuid.uuid4(), role="supervisor"),
            uuid.uuid4(),
        )

    def test_assigned_therapist_can_read_clinical_trace(self):
        _require_clinical_read(
            _Db(assignment=SimpleNamespace(status="active")),
            SimpleNamespace(id=uuid.uuid4(), role="therapist"),
            uuid.uuid4(),
        )

    def test_unassigned_therapist_is_denied(self):
        with self.assertRaises(HTTPException) as caught:
            _require_clinical_read(
                _Db(),
                SimpleNamespace(id=uuid.uuid4(), role="therapist"),
                uuid.uuid4(),
            )
        self.assertEqual(caught.exception.status_code, 403)

    def test_admin_clinical_is_denied_clinical_trace(self):
        with self.assertRaises(HTTPException) as caught:
            _require_clinical_read(
                _Db(),
                SimpleNamespace(id=uuid.uuid4(), role="admin_clinical"),
                uuid.uuid4(),
            )
        self.assertEqual(caught.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
