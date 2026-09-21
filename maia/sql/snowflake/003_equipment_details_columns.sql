-- Migration: the equipment-details block (schema_version 1.1.0 -> 1.2.0).
--
-- The SIS detail page publishes four values that the record now carries as
-- first-class columns rather than as free-form specifications: the machine's
-- own serial and build date, and the engine's. MACHINE_SERIAL_NUMBER is also
-- the cross-check — it must equal SERIAL_NUMBER, and a row where it does not is
-- a row that describes a different machine.
--
-- Safe to re-run: every statement is IF NOT EXISTS.
ALTER TABLE EQUIPMENT_DATA ADD COLUMN IF NOT EXISTS MACHINE_SERIAL_NUMBER STRING;
ALTER TABLE EQUIPMENT_DATA ADD COLUMN IF NOT EXISTS MACHINE_BUILD_DATE    STRING;
ALTER TABLE EQUIPMENT_DATA ADD COLUMN IF NOT EXISTS ENGINE_SERIAL_NUMBER  STRING;
ALTER TABLE EQUIPMENT_DATA ADD COLUMN IF NOT EXISTS ENGINE_BUILD_DATE     STRING;

COMMENT ON COLUMN EQUIPMENT_DATA.MACHINE_SERIAL_NUMBER IS
  'Serial as the source detail page itself shows it. Must equal SERIAL_NUMBER; a mismatch is quarantined upstream and never written.';
COMMENT ON COLUMN EQUIPMENT_DATA.MACHINE_BUILD_DATE IS
  'ISO-8601. Converted from the order the source publishes (SIS: MM/DD/YYYY).';
COMMENT ON COLUMN EQUIPMENT_DATA.ENGINE_BUILD_DATE IS
  'ISO-8601. Converted from the order the source publishes (SIS: MM/DD/YYYY).';

-- Rows written before this migration have no detail block. They are not wrong,
-- they are older: NULL means "not collected", never "the source has none".
