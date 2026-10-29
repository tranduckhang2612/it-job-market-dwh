"""Exact, versioned aliases and loss-aware atomic categorical values for both sources."""
from __future__ import annotations
import copy
from functools import lru_cache
from html import unescape
import json
import math
from pathlib import Path
import re
import unicodedata

CATALOG_PATH=Path(__file__).with_name('standardization_catalog.json')
PRIORITY={'unspecified':0,'preferred':1,'required':2}

def label_key(value):
    text=unicodedata.normalize('NFKC',unescape(str(value or '')))
    text=re.sub('[\u200b\u200c\u200d\u2060\ufeff]','',text)
    return re.sub(r'\s+',' ',text.casefold()).strip()

@lru_cache(maxsize=1)
def catalog():
    document=json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    maps={}
    for domain,rows in document['domains'].items():
        mapping={}
        for row in rows:
            for alias in [row['name'],*row['aliases']]:
                k=label_key(alias)
                if k in mapping and mapping[k]['name']!=row['name']:
                    raise ValueError('Conflicting canonical alias: '+domain+':'+k)
                mapping[k]=row
        maps[domain]=mapping
    return document,maps

def lookup(domain,value):
    return catalog()[1].get(domain,{}).get(label_key(value))

def canonical(domain,value):
    if value is None:return None
    row=lookup(domain,value)
    return row['name'] if row else str(value).strip()

def split_values(domain,value):
    """Known whole concepts win over separators; split only fully recognized lists."""
    if not value:return []
    if lookup(domain,value):return [canonical(domain,value)]
    parts=re.split(r'\s*(?:[,;|\n]|/|\s+(?:&|and|và)\s+)\s*',str(value))
    if len(parts)>1 and all(p and lookup(domain,p) for p in parts):
        return list(dict.fromkeys(canonical(domain,p) for p in parts))
    return [str(value).strip()]

def unresolved_composite(domain,value):
    return not lookup(domain,value) and bool(re.search(r'[,;|/\n]|\s&\s',str(value)))

def unique_values(domain,values):
    output=[];seen=set()
    for value in values or []:
        for name in split_values(domain,value):
            k=label_key(name)
            if k and k not in seen:output.append(name);seen.add(k)
    return output

def _number(value):
    if isinstance(value,str) and re.fullmatch(r'[+-]?\d+(?:\.\d+)?',value.strip()):
        n=float(value)
        if math.isfinite(n):return int(n) if n.is_integer() else n
    return value  # existing validators reject boolean/non-finite/ambiguous numbers

def standardize_record(job):
    result=copy.deepcopy(job)
    for field,domain in [('job_roles','role'),('seniority_levels','seniority'),('cities','city'),
                         ('required_knowledge','knowledge'),('soft_skills','soft_skill')]:
        result[field]=unique_values(domain,result.get(field,[]))
    for field,domain in [('work_mode','work_mode'),('salary_currency','currency'),
                         ('salary_period','salary_period'),('salary_basis','salary_basis')]:
        result[field]=canonical(domain,result.get(field))
    if isinstance(result.get('salary_currency'),str):result['salary_currency']=result['salary_currency'].upper()
    for field in ('experience_min_years','experience_max_years','salary_min','salary_max'):
        result[field]=_number(result.get(field))
    languages={}
    def language(name,level,requirement):
        for name in split_values('foreign_language',name):
            k=label_key(name)
            if k not in languages:
                languages[k]={'language':name,'level_text':level,'requirement_type':requirement}
            else:
                previous=languages[k]
                if PRIORITY.get(requirement,-1)>PRIORITY.get(previous['requirement_type'],-1):previous['requirement_type']=requirement
                levels=list(dict.fromkeys(x for x in [previous['level_text'],level] if x))
                previous['level_text']='; '.join(levels) or None
    for item in result.get('foreign_languages',[]):
        language(item['language'],item.get('level_text'),canonical('requirement',item.get('requirement_type','unspecified')))
    skills={}
    for item in result.get('skills',[]):
        requirement=canonical('requirement',item.get('requirement_type','unspecified'))
        # Exact mislabeled language/role tags move to the correct child relation.
        if lookup('foreign_language',item['name']):
            language(item['name'],None,requirement);continue
        if lookup('role',item['name']) and not lookup('skill',item['name']):
            result['job_roles']=unique_values('role',[*result['job_roles'],item['name']]);continue
        for name in split_values('skill',item['name']):
            row=lookup('skill',name)
            category=row['category'] if row else item.get('category','technical')
            k=label_key(name)
            if k not in skills:skills[k]={'name':name,'category':category,'requirement_type':requirement}
            elif PRIORITY.get(requirement,-1)>PRIORITY.get(skills[k]['requirement_type'],-1):skills[k]['requirement_type']=requirement
    result['skills']=list(skills.values())
    result['foreign_languages']=list(languages.values())
    education={}
    for item in result.get('education_requirements',[]):
        degrees=split_values('degree',item.get('degree')) if item.get('degree') else [None]
        majors=unique_values('major',item.get('majors',[]))
        requirement=canonical('requirement',item.get('requirement_type','unspecified'))
        for degree in degrees:
            k=(degree,tuple(sorted(label_key(m) for m in majors)))
            if k not in education:education[k]={'degree':degree,'majors':majors,'requirement_type':requirement}
            elif PRIORITY.get(requirement,-1)>PRIORITY.get(education[k]['requirement_type'],-1):education[k]['requirement_type']=requirement
    result['education_requirements']=list(education.values())
    return result
