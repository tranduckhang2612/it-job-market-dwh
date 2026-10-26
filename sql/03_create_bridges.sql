-- 03_create_bridges.sql
BEGIN;

-- Bridge_Observation_Role
CREATE TABLE IF NOT EXISTS dwh.bridge_observation_role (
    observationkey   BIGINT  NOT NULL REFERENCES dwh.fact_job_observation (observationkey),
    rolekey          INTEGER NOT NULL REFERENCES dwh.dim_role (rolekey),
    allocationweight NUMERIC(9, 6),
    PRIMARY KEY (observationkey, rolekey)
);

-- Bridge_Observation_Seniority
CREATE TABLE IF NOT EXISTS dwh.bridge_observation_seniority (
    observationkey   BIGINT  NOT NULL REFERENCES dwh.fact_job_observation (observationkey),
    senioritykey     INTEGER NOT NULL REFERENCES dwh.dim_seniority (senioritykey),
    allocationweight NUMERIC(9, 6),
    PRIMARY KEY (observationkey, senioritykey)
);

-- Bridge_Observation_Location
CREATE TABLE IF NOT EXISTS dwh.bridge_observation_location (
    observationkey   BIGINT  NOT NULL REFERENCES dwh.fact_job_observation (observationkey),
    locationkey      INTEGER NOT NULL REFERENCES dwh.dim_location (locationkey),
    allocationweight NUMERIC(9, 6),
    PRIMARY KEY (observationkey, locationkey)
);

-- Bridge_Observation_Skill
CREATE TABLE IF NOT EXISTS dwh.bridge_observation_skill (
    observationkey   BIGINT  NOT NULL REFERENCES dwh.fact_job_observation (observationkey),
    skillkey         INTEGER NOT NULL REFERENCES dwh.dim_skill (skillkey),
    requirementtype  VARCHAR(20) NOT NULL,
    allocationweight NUMERIC(9, 6),
    PRIMARY KEY (observationkey, skillkey)
);

-- Bridge_Observation_Knowledge
CREATE TABLE IF NOT EXISTS dwh.bridge_observation_knowledge (
    observationkey   BIGINT  NOT NULL REFERENCES dwh.fact_job_observation (observationkey),
    knowledgekey     INTEGER NOT NULL REFERENCES dwh.dim_knowledge (knowledgekey),
    allocationweight NUMERIC(9, 6),
    PRIMARY KEY (observationkey, knowledgekey)
);

-- Bridge_Observation_Education
CREATE TABLE IF NOT EXISTS dwh.bridge_observation_education (
    observationkey   BIGINT  NOT NULL REFERENCES dwh.fact_job_observation (observationkey),
    educationkey     INTEGER NOT NULL REFERENCES dwh.dim_education (educationkey),
    requirementtype  VARCHAR(20) NOT NULL,
    PRIMARY KEY (observationkey, educationkey)
);

-- Bridge_Observation_Language
CREATE TABLE IF NOT EXISTS dwh.bridge_observation_language (
    observationkey   BIGINT  NOT NULL REFERENCES dwh.fact_job_observation (observationkey),
    languagekey      INTEGER NOT NULL REFERENCES dwh.dim_foreign_language (languagekey),
    leveltext        TEXT,
    requirementtype  VARCHAR(20) NOT NULL,
    PRIMARY KEY (observationkey, languagekey)
);

-- Bridge_Observation_SoftSkill
CREATE TABLE IF NOT EXISTS dwh.bridge_observation_softskill (
    observationkey   BIGINT  NOT NULL REFERENCES dwh.fact_job_observation (observationkey),
    softskillkey     INTEGER NOT NULL REFERENCES dwh.dim_soft_skill (softskillkey),
    allocationweight NUMERIC(9, 6),
    PRIMARY KEY (observationkey, softskillkey)
);

COMMIT;
