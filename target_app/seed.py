"""Seed data and the variant string table for the stand-in application.

Every user-visible string lives in VARIANTS. Templates read from it rather than
hardcoding text, so a second tenant variant can later be added as another entry in
this dict instead of as a forked set of templates.

All member data here is fabricated. No real person, account, or institution.
"""
from __future__ import annotations

from typing import Any

VARIANTS: dict[str, dict[str, Any]] = {
    "a": {
        "variant_id": "a",
        "brand": "Cedar Ridge Credit Union",
        "console": "Member Services Console",
        "footer": "Internal system. Authorized staff only.",
        "titles": {
            "home": "Home",
            "search": "Member Lookup",
            "detail": "Member Detail",
            "not_found": "Member Not Found",
            "denied": "Access Restricted",
            "subaccount_form": "Open Sub-Account",
            "subaccount_review": "Review Sub-Account Request",
            "subaccount_confirm": "Sub-Account Opened",
            "maintenance": "Scheduled Maintenance",
            "session_expired": "Session Expired",
            "error": "System Error",
            "faults": "Fault Console",
            "loan_servicing": "Loan Servicing",
        },
        "labels": {
            "nav_home": "Home",
            "nav_search": "Member Lookup",
            "member_id": "Member ID",
            "search_submit": "Search",
            "member_name": "Member Name",
            "member_status": "Status",
            "member_branch": "Home Branch",
            "deposit_heading": "Deposit Accounts",
            "loan_heading": "Loan Accounts",
            "col_type": "Account Type",
            "col_number": "Account Number",
            "col_balance": "Current Balance",
            "select": "Select",
            "open_subaccount": "Open Sub-Account",
            "account_type": "Account Type",
            "nickname": "Nickname",
            "initial_deposit": "Initial Deposit",
            "statement_delivery": "Statement Delivery",
            "submit_request": "Submit Request",
            "confirm": "Confirm",
            "continue_label": "Continue",
            "new_account_number": "New Account Number",
            "back_to_member": "Back to Member Detail",
            "arm": "Arm",
            "clear": "Clear Armed Fault",
            "currently_armed": "Currently armed",
            "none_armed": "Nothing armed",
        },
        "messages": {
            "home_intro": "Select Member Lookup to begin servicing a member record.",
            "not_found": "No member record matches that Member ID.",
            "denied": "This member record is restricted. You do not have permission to view it.",
            "maintenance": "This system is undergoing scheduled maintenance.",
            "session_expired": "Your session has expired. Please sign in again.",
            "error": "An unexpected error occurred while processing your request.",
            "confirmed": "The sub-account has been opened.",
            "review_intro": "Review the request below and select Confirm to open the account.",
            "faults_intro": "Arm a runtime fault. It fires once on the next page load, then disarms.",
            "loan_servicing": "Loan servicing is not available in this console. A sub-account cannot be opened against a loan account.",
        },
        "errors": {
            "member_id_required": "Enter a Member ID.",
            "nickname_required": "Nickname is required.",
            "deposit_required": "Initial deposit is required.",
            "deposit_numeric": "Initial deposit must be a number.",
            "deposit_positive": "Initial deposit must be greater than zero.",
            "account_type_required": "Select an account type.",
        },
        "account_types": ["Savings", "Money Market", "Holiday Club"],
        "statement_options": ["Paperless", "Mailed"],
    }
}

# member_id -> record. 100003 is restricted and returns permission denied.
# Any id absent from this dict returns the no-member-found screen.
MEMBERS: dict[str, dict[str, Any]] = {
    "100001": {
        "member_id": "100001",
        "name": "Marcus Webb",
        "status": "Active",
        "branch": "Cedar Ridge Main",
        "restricted": False,
        "deposit_accounts": [
            {"type": "Savings", "masked": "xxxxxx4417", "balance": "4,182.55"},
            {"type": "Checking", "masked": "xxxxxx2093", "balance": "1,247.10"},
        ],
        "loan_accounts": [
            {"type": "Auto Loan", "masked": "xxxxxx8810", "balance": "11,904.32"},
        ],
    },
    "100002": {
        "member_id": "100002",
        "name": "Dana Ruiz",
        "status": "Active",
        "branch": "Northgate",
        "restricted": False,
        "deposit_accounts": [
            {"type": "Savings", "masked": "xxxxxx7731", "balance": "912.04"},
        ],
        "loan_accounts": [],
    },
    "100003": {
        "member_id": "100003",
        "name": "Priya Shah",
        "status": "Restricted",
        "branch": "Cedar Ridge Main",
        "restricted": True,
        "deposit_accounts": [],
        "loan_accounts": [],
    },
    "100004": {
        "member_id": "100004",
        "name": "Alan Whitfield",
        "status": "Active",
        "branch": "Southline",
        "restricted": False,
        "deposit_accounts": [
            {"type": "Savings", "masked": "xxxxxx1180", "balance": "26,540.19"},
            {"type": "Money Market", "masked": "xxxxxx6602", "balance": "58,110.00"},
        ],
        "loan_accounts": [
            {"type": "Home Equity", "masked": "xxxxxx3345", "balance": "42,000.00"},
        ],
    },
}
