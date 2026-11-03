"""Turn canonical v5.1 arrays into atomic dimension/bridge records.

No crawler, role gate, upstream normalizer or database writes are invoked here.
Unknown composites are retained in review issues, not split by guesswork.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata

VERSION = '1.0'
SOURCE_VERSION = '5.1'
CATALOG = Path(__file__).with_name('catalog.json')
GROUPS = ('programming_language', 'framework', 'core_skill', 'role', 'city',
          'foreign_language', 'level', 'soft_skill')
SINGULAR = dict(zip(GROUPS, ('programming_language', 'framework', 'core_skill', 'role',
                           'city', 'foreign_language', 'level', 'soft_skill')))
SCALARS = ('job_id', 'source', 'source_job_id', 'job_url', 'crawl_run_id', 'observed_at',
           'title', 'company_name', 'description', 'location_text', 'work_mode')


def encode(value):
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError('Datetime must include timezone')
        return value.astimezone(timezone.utc).isoformat()
    raise TypeError(type(value).__name__)


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=encode, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Expected nonempty normalized text')
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', value)).strip()


def label_key(value):
    return unicodedata.normalize('NFKC', text(value)).casefold()


def catalog():
    result = {}
    for group, rows in json.loads(CATALOG.read_text(encoding='utf-8')).items():
        mapping = {}
        for row in rows:
            for alias in [row['name'], *row.get('aliases', [])]:
                key = label_key(alias)
                if key in mapping and mapping[key] != row:
                    raise ValueError('Conflicting alias: '+alias)
                mapping[key] = row
        result[group] = mapping
    return result


def atomize(job):
    if job['normalization_version'] != SOURCE_VERSION:
        raise ValueError('Expected normalize_data contract '+SOURCE_VERSION)
    scalar = {name: job[name] for name in SCALARS}
    for name in ('job_id', 'source', 'job_url', 'crawl_run_id', 'title', 'description'):
        text(scalar[name])
    if scalar['source'] not in ('itviec', 'topdev') or not scalar['job_id'].startswith(scalar['source']+':'):
        raise ValueError('Source-qualified job identity mismatch')
    for field in ('observed_at', 'normalized_at'):
        if not isinstance(job[field], datetime) or job[field].utcoffset() is None:
            raise ValueError(field+' must include timezone')
    if scalar['work_mode'] not in (None, 'onsite', 'hybrid', 'remote'):
        raise ValueError('Unsupported normalized work_mode')
    oid = digest([job['job_id'], job['crawl_run_id']])
    maps = catalog()
    values = {group: {} for group in GROUPS}
    issues = {}

    def issue(field, value, reason):
        key = digest([field, value, reason])
        issues[key] = {'issue_key': key, 'field_name': field, 'source_value': value, 'reason': reason}

    def add(group, name, category='', level=None):
        name = text(name)
        category = text(category) if category else ''
        row = maps.get(group, {}).get(label_key(name))
        if row:
            name = row['name']
            category = row.get('category', category)
        # Unknown spellings are not silently merged by case/fuzzy matching.
        identity = label_key(name) if row else name
        key = digest([group, identity, category])
        context = digest(level) if group == 'foreign_language' else ''
        values[group][(key, context)] = {'value_key': key, 'name': name, 'category': category,
                                        'context_key': context, 'level_text': level}

    def split(group, value):
        name = text(value)
        lookup = maps.get(group, {})
        if group in ('programming_language', 'framework', 'core_skill'):
            lookup = {k: v for g in ('programming_language', 'framework', 'core_skill') for k, v in maps[g].items()}
        if label_key(name) in lookup:
            return [lookup[label_key(name)]['name']]
        parts = re.split(r'\s*(?:[,;|/\n]|\s+(?:&|and|và)\s+)\s*', name)
        if len(parts) > 1:
            if all(p and label_key(p) in lookup for p in parts):
                return [lookup[label_key(p)]['name'] for p in parts]
            issue(group, name, 'unresolved_composite')
            return []
        return [name]

    for field, group in [('job_roles', 'role'), ('cities', 'city'), ('seniority_levels', 'level'), ('soft_skills', 'soft_skill')]:
        if not isinstance(job[field], list):
            raise ValueError(field+' must be an array')
        for value in job[field]:
            for name in split(group, value):
                add(group, name)
    if not job['job_roles']:
        raise ValueError('Normalized job must have a role')
    if not isinstance(job['skills'], list):
        raise ValueError('skills must be an array')
    skill_categories = {
        'programming_language': 'programming_language', 'programming_languages': 'programming_language',
        'framework_library': 'framework', 'frameworks_libraries': 'framework', 'framework': 'framework',
    }
    for item in job['skills']:
        if not isinstance(item, dict) or set(item) != {'name', 'category'}:
            raise ValueError('Skill must have exactly name/category')
        name, category = text(item['name']), text(item['category'])
        if label_key(name) in maps.get('foreign_language', {}):
            add('foreign_language', name)
            issue('skills', name, 'foreign_language_tag_moved')
            continue
        if label_key(name) in maps.get('role', {}):
            add('role', name)
            issue('skills', name, 'role_tag_moved')
            continue
        if label_key(name) in maps.get('soft_skill', {}):
            add('soft_skill', name)
            issue('skills', name, 'soft_skill_tag_moved')
            continue
        if label_key(name) in maps.get('business_domain', {}):
            issue('skills', name, 'business_domain_not_a_skill')
            continue
        # Known names take precedence over inconsistent source category labels.
        known = next((g for g in ('programming_language', 'framework', 'core_skill')
                      if label_key(name) in maps.get(g, {})), None)
        group = known or skill_categories.get(category, 'core_skill')
        for atom in split(group, name):
            other = next((g for g in ('programming_language', 'framework', 'core_skill')
                          if label_key(atom) in maps.get(g, {})), group)
            cat = {'programming_language': 'programming_language', 'framework': 'framework_library'}.get(other, category)
            add(other, atom, cat)
            if other == 'core_skill' and category in ('other', 'domain'):
                issue('skills', name, 'category_needs_review')
    if not isinstance(job['foreign_languages'], list) or not job['foreign_languages']:
        raise ValueError('foreign_languages must be a nonempty array')
    unknown_language = False
    for item in job['foreign_languages']:
        if not isinstance(item, dict) or set(item) != {'language', 'level_text'}:
            raise ValueError('Foreign language must have language/level_text')
        name = text(item['language'])
        if label_key(name) == 'unknown':
            unknown_language = True
            continue
        level = item['level_text']
        if level is not None:
            level = text(level)
        # A certificate requirement remains one statement; no CEFR inference.
        for atom in split('foreign_language', name):
            add('foreign_language', atom, level=level)
    scalar['foreign_language_status'] = 'known' if values['foreign_language'] else 'unknown'
    if unknown_language:
        issue('foreign_languages', 'unknown', 'source_did_not_specify_language')
    result = {'observation_key': oid, 'scalar': scalar,
              'groups': {g: sorted(rows.values(), key=lambda r: (r['value_key'], r['context_key'])) for g, rows in values.items()},
              'issues': sorted(issues.values(), key=lambda i: i['issue_key'])}
    return result
