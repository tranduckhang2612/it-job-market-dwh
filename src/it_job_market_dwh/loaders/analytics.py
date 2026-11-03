"""Read normalize_data v5.1 and publish atomic analytics tables (no benefit)."""
from __future__ import annotations
import argparse
import getpass
import json
from pathlib import Path

from it_job_market_dwh.analytics import storage as s
from it_job_market_dwh.loaders import postgres


class AnalyticsStorageError(RuntimeError):
    """Cannot connect to the analytics PostgreSQL source."""


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    postgres.add_scope_arguments(parser)
    postgres.add_connection_arguments(parser)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--check-db', action='store_true')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='Publish/sync selected source rows into analytics.')
    mode.add_argument('--rollback', type=Path, help='Restore analytics from a successful --out snapshot directory.')
    parser.add_argument('--out', type=Path, help='New local snapshot/report directory; required for apply.')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit is not None:
        parser.error('No --limit for analytics sync; choose --source or --crawl-run-id instead')
    if args.apply and (args.dry_run or args.check_db):
        parser.error('Choose apply or read-only check/dry-run')
    if args.apply and (args.out is None or args.out.exists()):
        parser.error('Apply requires a new --out directory')
    if args.rollback and (args.source or args.crawl_run_id or args.dry_run or args.check_db):
        parser.error('Rollback uses its recorded full scope')
    if args.crawl_run_id is not None:
        args.crawl_run_id = args.crawl_run_id.strip()
        if not args.crawl_run_id:
            parser.error('crawl-run-id must not be empty')
    args.db_password = None
    conn = None
    try:
        postgres.configure_compose_database(args, AnalyticsStorageError)
        if args.db_password_prompt:
            args.db_password = getpass.getpass('Mật khẩu PostgreSQL: ')
        conn = postgres.connect(args, 'analytics_loader', AnalyticsStorageError)
        if args.rollback:
            before = json.loads((args.rollback/'before.json').read_text(encoding='utf-8'))
            after = json.loads((args.rollback/'after.json').read_text(encoding='utf-8'))
            s.rollback(conn, before, after)
            print('ROLLBACK_OK analytics_restored=true upstream_writes=0')
        elif args.apply:
            metrics, _, _ = s.apply(conn, args.crawl_run_id, args.source, out=args.out)
            print('APPLY_OK '+json.dumps(metrics, sort_keys=True))
        else:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                items, deleted = s.plan(conn, args.crawl_run_id, args.source)
                result = {'input_rows': len(items), 'pending': sum(i['kind'] != 'unchanged' for i in items), 'pending_deletions': len(deleted), 'writes': 0}
            if args.out:
                s.write_json(args.out/'check.json', result)
            print('CHECK_OK '+json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # Never print DSNs/connection strings or source payloads in errors.
        print('ANALYTICS_ERROR '+type(exc).__name__+': '+str(exc) if isinstance(exc, ValueError) else 'ANALYTICS_ERROR '+type(exc).__name__)
        return 4
    finally:
        args.db_password = None
        if conn is not None:
            conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
