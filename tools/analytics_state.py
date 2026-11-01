"""Snapshot helpers for the managed analytics layer; no upstream writes."""
from psycopg import sql
from tools import clean_staging as c

def views(conn,schema):
    defs={n:d for n,d in conn.execute('SELECT viewname,definition FROM pg_views WHERE schemaname=%s ORDER BY viewname',(schema,))}
    deps={n:set() for n in defs}
    for n,parent in conn.execute('''SELECT DISTINCT v.relname,p.relname FROM pg_rewrite r
      JOIN pg_class v ON v.oid=r.ev_class JOIN pg_namespace vn ON vn.oid=v.relnamespace
      JOIN pg_depend d ON d.classid='pg_rewrite'::regclass AND d.objid=r.oid
      JOIN pg_class p ON p.oid=d.refobjid JOIN pg_namespace pn ON pn.oid=p.relnamespace
      WHERE vn.nspname=%s AND pn.nspname=%s AND p.relkind='v' AND v.oid<>p.oid''',(schema,schema)):
        deps[n].add(parent)
    order=[]
    while deps:
        ready=sorted(n for n,d in deps.items() if not d.intersection(deps))
        if not ready:raise ValueError('View dependency cycle')
        order+=ready
        for n in ready:del deps[n]
    return {'definitions':defs,'order':order}

def layer(conn,schema):
    if not conn.execute('SELECT to_regnamespace(%s)',(schema,)).fetchone()[0]:return {'exists':False}
    names=[r[0] for r in conn.execute('SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename',(schema,))]
    tables={n:[r[0] for r in conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {}.{} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(schema),sql.Identifier(n)))] for n in names}
    functions=[r[0] for r in conn.execute('''SELECT pg_get_functiondef(p.oid) FROM pg_proc p
      JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s ORDER BY proname''',(schema,))]
    return {'exists':True,'tables':tables,'views':views(conn,schema),'functions':functions}


def upstream(conn):
    result = {"content": c.snapshot(conn), "xmin": {}}
    for table in c.TABLES:
        result["xmin"][table] = conn.execute(sql.SQL(
            "SELECT xmin::text,to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text"
        ).format(sql.Identifier(*table.split(".")))).fetchall()
    return result

def snapshot(conn):
    return {"upstream": upstream(conn), "analytics": layer(conn,"analytics")}
