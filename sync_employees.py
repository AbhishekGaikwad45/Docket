#!/usr/bin/env python
"""
Employee Synchronization CLI Script
Syncs employees from MS SQL Server (JSW_Dharamtar) into PostgreSQL (docket_db).

Usage:
    python sync_employees.py                # Syncs both Staff and Associates
    python sync_employees.py --active-only  # Syncs only Active employees
    python sync_employees.py --staff-only   # Syncs only Staff
    python sync_employees.py --assoc-only   # Syncs only Associates
"""
import argparse
import sys
from sync_service import sync_employees


def main():
    parser = argparse.ArgumentParser(description="Sync employees from SQL Server to PostgreSQL.")
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Sync only employees with status 'Active'",
    )
    parser.add_argument(
        "--staff-only",
        action="store_true",
        help="Sync only Staff records",
    )
    parser.add_argument(
        "--assoc-only",
        action="store_true",
        help="Sync only Associates records",
    )

    args = parser.parse_args()

    include_staff = not args.assoc_only
    include_associates = not args.staff_only

    print("=" * 65)
    print("  DOCKET - EMPLOYEE MASTER SYNCHRONIZATION")
    print("=" * 65)
    print(f"  Filter: {'Active Only' if args.active_only else 'All Statuses'}")
    print(f"  Sources: {'Staff ' if include_staff else ''}{'Associates' if include_associates else ''}")
    print("-" * 65)

    result = sync_employees(
        include_staff=include_staff,
        include_associates=include_associates,
        active_only=args.active_only,
    )

    print("-" * 65)
    if result["success"]:
        print("  Status: SUCCESS")
        print(f"  Staff records fetched:      {result['staff_fetched']}")
        print(f"  Associates records fetched: {result['associates_fetched']}")
        print(f"  Total records upserted:     {result['total_upserted']}")
        print(f"  Duration:                   {result['duration_seconds']}s")
    else:
        print("  Status: FAILED / PARTIAL")
        for err in result["errors"]:
            print(f"  Error: {err}")
        sys.exit(1)
    print("=" * 65)


if __name__ == "__main__":
    main()
