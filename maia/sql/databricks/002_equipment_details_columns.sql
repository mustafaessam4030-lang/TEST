-- Migration: the equipment-details block (schema_version 1.1.0 -> 1.2.0).
-- See sql/snowflake/003_equipment_details_columns.sql for the rationale.
ALTER TABLE equipment_data ADD COLUMNS (
  machine_serial_number STRING COMMENT 'Serial as the detail page shows it; must equal serial_number',
  machine_build_date    STRING COMMENT 'ISO-8601, converted from the source order (SIS: MM/DD/YYYY)',
  engine_serial_number  STRING,
  engine_build_date     STRING COMMENT 'ISO-8601, converted from the source order (SIS: MM/DD/YYYY)'
);
