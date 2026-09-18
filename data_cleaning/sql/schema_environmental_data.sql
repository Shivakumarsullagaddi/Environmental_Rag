-- Structured SQL tables for Phase 11: Environmental Datasets
-- Numerical/spatial environmental observations queried directly with SQL

CREATE TABLE IF NOT EXISTS `developer-491706.darukaa_kb.soil_data` (
  record_id STRING NOT NULL,
  latitude FLOAT64 NOT NULL,
  longitude FLOAT64 NOT NULL,
  date DATE NOT NULL,
  soil_ph FLOAT64,
  organic_carbon FLOAT64,
  soil_moisture FLOAT64,
  source_dataset STRING
);

CREATE TABLE IF NOT EXISTS `developer-491706.darukaa_kb.climate_data` (
  record_id STRING NOT NULL,
  latitude FLOAT64 NOT NULL,
  longitude FLOAT64 NOT NULL,
  date DATE NOT NULL,
  temperature FLOAT64,
  rainfall FLOAT64,
  humidity FLOAT64,
  source_dataset STRING
);

CREATE TABLE IF NOT EXISTS `developer-491706.darukaa_kb.lulc_data` (
  record_id STRING NOT NULL,
  latitude FLOAT64 NOT NULL,
  longitude FLOAT64 NOT NULL,
  year INT64 NOT NULL,
  land_cover STRING,
  confidence FLOAT64,
  source_dataset STRING
);

CREATE TABLE IF NOT EXISTS `developer-491706.darukaa_kb.biodiversity_data` (
  record_id STRING NOT NULL,
  latitude FLOAT64 NOT NULL,
  longitude FLOAT64 NOT NULL,
  species STRING NOT NULL,
  observation_date DATE,
  kingdom STRING,
  family STRING,
  count INT64,
  source_dataset STRING
);
