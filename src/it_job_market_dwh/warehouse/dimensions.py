"""Read existing analytics dimensions; load SQL Server without re-normalizing.

SCD1 for an existing analytics key, insert new keys, retain missing keys.
Surrogate IDs and load timestamps survive an identical repeat load.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from it_job_market_dwh.paths import SQL_ROOT

GROUPS = ('programming_language', 'framework', 'core_skill', 'role', 'city',
          'foreign_language', 'level', 'soft_skill')
MODEL = 'sqlserver_dimensions_v1'
DDL = SQL_ROOT / 'dw_dimensions_sqlserver.sql'
COLUMNS = ('dim_id', 'analytics_key', 'name', 'category', 'row_sha256',
           'created_at_utc', 'updated_at_utc')


def encode(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec='microseconds')
    raise TypeError(type(value).__name__)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=encode,
                      separators=(',', ':'))


def digest(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               default=encode, indent=2), encoding='utf-8')


def validate_source(source):
    if not isinstance(source, dict) or set(source) != set(GROUPS):
        raise ValueError('Expected all eight analytics dimension groups')
    result = {}
    for group in GROUPS:
        keys, labels, rows = set(), set(), []
        if not isinstance(source[group], list):
            raise ValueError('Dimension group must be a list')
        for row in source[group]:
            if not isinstance(row, dict) or set(row) != {'analytics_key', 'name', 'category'}:
                raise ValueError('Dimension must contain exactly key/name/category')
            key, name, category = (row[k] for k in ('analytics_key', 'name', 'category'))
            if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key):
                raise ValueError('Invalid analytics key')
            for value, limit, empty in ((name, 512, False), (category, 128, True)):
                if not isinstance(value, str) or value != value.strip() or '\x00' in value:
                    raise ValueError('Expected scalar trimmed Unicode text')
                if (not empty and not value) or len(value.encode('utf-16-le')) // 2 > limit:
                    raise ValueError('Text empty or exceeds SQL Server capacity')
            if key in keys or (name, category) in labels:
                raise ValueError('Duplicate dimension key or natural value')
            keys.add(key)
            labels.add((name, category))
            rows.append(dict(row))
        result[group] = sorted(rows, key=lambda x: x['analytics_key'])
    return result


def read_source(pg):
    """One read-only PostgreSQL snapshot; only analytics SELECT privileges needed."""
    from psycopg import sql
    with pg.transaction():
        pg.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        model = pg.execute('SELECT model_name FROM analytics.pipeline_state WHERE singleton').fetchone()
        if model != ('normalize_atomic_v1',):
            raise ValueError('Expected normalize_atomic_v1 analytics model')
        source = {}
        for group in GROUPS:
            query = sql.SQL('SELECT {},name,category FROM analytics.{} ORDER BY {}').format(
                sql.Identifier(group + '_key'), sql.Identifier('dim_' + group),
                sql.Identifier(group + '_key'))
            source[group] = [dict(zip(('analytics_key', 'name', 'category'), row))
                             for row in pg.execute(query).fetchall()]
    return validate_source(source)


def _metadata(conn, group):
    table = 'dw.dim_' + group
    cursor = conn.cursor()
    rows = cursor.execute("""SELECT c.name,t.name,c.max_length,c.is_nullable,c.is_identity,
        c.scale,c.collation_name FROM sys.columns c JOIN sys.types t ON c.user_type_id=t.user_type_id
        WHERE c.object_id=OBJECT_ID(?) ORDER BY c.column_id""", table).fetchall()
    if not rows:
        return None
    expected = [('dim_id', 'int', 4, False, True, 0, None),
                ('analytics_key', 'char', 64, False, False, 0, 'Latin1_General_100_BIN2'),
                ('name', 'nvarchar', 1024, False, False, 0, 'Latin1_General_100_BIN2'),
                ('category', 'nvarchar', 256, False, False, 0, 'Latin1_General_100_BIN2'),
                ('row_sha256', 'char', 64, False, False, 0, 'Latin1_General_100_BIN2'),
                ('created_at_utc', 'datetime2', 8, False, False, 6, None),
                ('updated_at_utc', 'datetime2', 8, False, False, 6, None)]
    if [tuple(r) for r in rows] != expected:
        raise ValueError('Incompatible DW dimension column contract: ' + group)
    indexes = cursor.execute("""SELECT i.index_id,i.is_primary_key,c.name,ic.key_ordinal
        FROM sys.indexes i JOIN sys.index_columns ic ON i.object_id=ic.object_id AND i.index_id=ic.index_id
        JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
        WHERE i.object_id=OBJECT_ID(?) AND i.is_unique=1 AND i.has_filter=0 AND ic.key_ordinal>0
        ORDER BY i.index_id,ic.key_ordinal""", table).fetchall()
    unique = {}
    primary = []
    for index, is_primary, col, _ in indexes:
        unique.setdefault(index, []).append(col)
        if is_primary:
            primary.append(col)
    if primary != ['dim_id'] or ['analytics_key'] not in unique.values() or ['name', 'category'] not in unique.values():
        raise ValueError('Missing DW dimension primary/unique keys: ' + group)
    return [list(r) for r in rows]


def snapshot(conn):
    cursor = conn.cursor()
    server, database = cursor.execute('SELECT CAST(SERVERPROPERTY(\'ServerName\') AS nvarchar(128)),DB_NAME()').fetchone()
    result = {'model': MODEL, 'server': server, 'database': database,
              'schema_exists': bool(cursor.execute("SELECT COUNT(*) FROM sys.schemas WHERE name=N'dw'").fetchone()[0]),
              'tables': {}}
    for group in GROUPS:
        metadata = _metadata(conn, group)
        if metadata is None:
            result['tables'][group] = None
            continue
        rows = cursor.execute('SELECT ' + ','.join(COLUMNS) + ' FROM dw.dim_' + group + ' ORDER BY dim_id').fetchall()
        identity = cursor.execute('SELECT CONVERT(bigint,last_value) FROM sys.identity_columns WHERE object_id=OBJECT_ID(?)',
                                  'dw.dim_' + group).fetchone()[0]
        result['tables'][group] = {'columns': metadata, 'identity': identity,
                                  'rows': [dict(zip(COLUMNS, r)) for r in rows]}
    # Normalize datetimes so JSON receipts and live state compare identically.
    return json.loads(dumps(result))


def plan(source, state):
    source = validate_source(source)
    if state.get('model') != MODEL or set(state['tables']) != set(GROUPS):
        raise ValueError('Invalid DW state model')
    changes, metrics = {}, {}
    for group in GROUPS:
        old = {r['analytics_key']: r for r in (state['tables'][group] or {}).get('rows', [])}
        natural = {(r['name'], r['category']): r['analytics_key'] for r in old.values()}
        changes[group] = []
        counts = dict(inserted=0, updated=0, unchanged=0, retained=0)
        for row in source[group]:
            owner = natural.get((row['name'], row['category']))
            if owner is not None and owner != row['analytics_key']:
                raise ValueError('Natural value already belongs to another analytics key: ' + group)
            row = dict(row, row_sha256=digest([group, row['analytics_key'], row['name'], row['category']]))
            prior = old.pop(row['analytics_key'], None)
            kind = ('inserted' if prior is None else
                    'unchanged' if all(prior[k] == row[k] for k in row) else 'updated')
            counts[kind] += 1
            if kind != 'unchanged':
                changes[group].append((kind, row))
        counts['retained'] = len(old)
        metrics[group] = counts
    return changes, metrics


def _lock(conn):
    if conn.autocommit:
        raise ValueError('DW write connection must use autocommit=False')
    cursor = conn.cursor()
    cursor.execute('SET NOCOUNT ON; SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;')
    cursor.execute('SELECT COUNT(*) FROM sys.tables').fetchone()
    code = cursor.execute("""DECLARE @r int; EXEC @r=sys.sp_getapplock
        @Resource=N'it_job_market_dimensions_v1',@LockMode='Exclusive',
        @LockOwner='Transaction',@LockTimeout=10000; SELECT @r;""").fetchone()[0]
    if code < 0:
        raise RuntimeError('DW dimension load lock not acquired')


def apply(conn, source, out, *, fail_after_write=False):
    source = validate_source(source)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    try:
        _lock(conn)
        before = snapshot(conn)
        changes, metrics = plan(source, before)
        write_json(out / 'before.json', before)
        if any(before['tables'][g] is None for g in GROUPS):
            conn.cursor().execute(DDL.read_text(encoding='utf-8'))
        cursor = conn.cursor()
        for group in GROUPS:
            for kind, row in changes[group]:
                if kind == 'inserted':
                    cursor.execute('INSERT INTO dw.dim_' + group +
                                   '(analytics_key,name,category,row_sha256) VALUES(?,?,?,?)',
                                   row['analytics_key'], row['name'], row['category'], row['row_sha256'])
                else:
                    cursor.execute('UPDATE dw.dim_' + group +
                                   ' SET name=?,category=?,row_sha256=?,updated_at_utc=SYSUTCDATETIME() WHERE analytics_key=?',
                                   row['name'], row['category'], row['row_sha256'], row['analytics_key'])
        if fail_after_write:
            raise RuntimeError('injected_dw_atomicity_failure')
        after = snapshot(conn)
        result = {'model': MODEL, 'source_sha256': digest(source), 'dimensions': metrics,
                  'rows': {g: len(after['tables'][g]['rows']) for g in GROUPS},
                  'before_sha256': digest(before), 'after_sha256': digest(after),
                  'facts_created': 0, 'bridges_created': 0}
        write_json(out / 'after.json', after)
        write_json(out / 'metrics.json', result)
        conn.commit()
        (out / 'COMMITTED').write_text('SQL Server transaction committed\n', encoding='utf-8')
        return result
    except Exception:
        conn.rollback()
        raise


def rollback(conn, receipt):
    receipt = Path(receipt)
    if not (receipt / 'COMMITTED').is_file():
        raise ValueError('Rollback requires a committed load receipt')
    before, after, metrics = [json.loads((receipt / n).read_text(encoding='utf-8'))
                              for n in ('before.json', 'after.json', 'metrics.json')]
    if (digest(before) != metrics['before_sha256'] or digest(after) != metrics['after_sha256']
            or before['model'] != MODEL or after['model'] != MODEL):
        raise ValueError('Rollback receipt hash/model mismatch')
    try:
        _lock(conn)
        if snapshot(conn) != after:
            raise ValueError('DW changed since this receipt, or target differs')
        cursor = conn.cursor()
        for group in reversed(GROUPS):
            original = before['tables'][group]
            if original is None:
                cursor.execute('DROP TABLE dw.dim_' + group)
                continue
            # Refuse a destructive table rebuild when another table references it.
            if cursor.execute('SELECT COUNT(*) FROM sys.foreign_keys WHERE referenced_object_id=OBJECT_ID(?)',
                              'dw.dim_' + group).fetchone()[0]:
                raise ValueError('Referenced dimension requires a coordinated warehouse rollback')
            cursor.execute('DELETE FROM dw.dim_' + group)
            cursor.execute('SET IDENTITY_INSERT dw.dim_' + group + ' ON')
            for row in original['rows']:
                values = [datetime.fromisoformat(row[k]) if k.endswith('_utc') else row[k] for k in COLUMNS]
                cursor.execute('INSERT INTO dw.dim_' + group + '(' + ','.join(COLUMNS) + ') VALUES(?,?,?,?,?,?,?)', values)
            cursor.execute('SET IDENTITY_INSERT dw.dim_' + group + ' OFF')
            if original['identity'] is None:
                cursor.execute('TRUNCATE TABLE dw.dim_' + group)
            else:
                identity = int(original['identity'])
                cursor.execute("DBCC CHECKIDENT ('dw.dim_" + group + "', RESEED, " + str(identity) + ') WITH NO_INFOMSGS')
        if not before['schema_exists']:
            cursor.execute('DROP SCHEMA dw')
        if snapshot(conn) != before:
            raise RuntimeError('Rollback state verification failed')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
