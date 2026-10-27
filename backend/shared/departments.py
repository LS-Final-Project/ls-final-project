"""Department codes shared by C/S routing, transfer and manager scope.

The source is identity.departments (infra/migrations/platform/20261023_KAN-78_manager_scopes.sql).
Business services cannot read the identity schema, so the codes are repeated here and a test
keeps both lists equal.
"""

from typing import Literal, get_args

# The C/S department. Tickets are transferred from it, never to it.
CS_DEPARTMENT = "GCS_L1"

TransferDepartment = Literal[
    "TS_LV",
    "TS_MV",
    "TS_TRF",
    "TS_AUTO",
    "TS_ENERGY",
    "RND_FW",
    "QA_CLAIM",
    "FIELD_SVC",
    "CERT",
    "SALES_OPS",
    "SCM",
    "WARRANTY_DESK",
]
TRANSFER_DEPARTMENTS: frozenset[str] = frozenset(get_args(TransferDepartment))
