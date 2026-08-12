/* =====================================================================
   03_source_grants.sql
   ---------------------------------------------------------------------
   Least-privilege read account for ADF on the Oracle source.
   Run as a DBA. Repeat the SELECT grants for every source schema.
   ===================================================================== */

CREATE USER adf_reader IDENTIFIED BY "<change-me>"
    DEFAULT TABLESPACE users
    QUOTA 0 ON users;                  -- the account never writes

GRANT CREATE SESSION TO adf_reader;

-- Data dictionary access for the structure-discovery step.
-- ALL_TAB_COLUMNS / ALL_TABLES are visible by default for objects the
-- account can select from, so the SELECT grants below are what actually
-- makes a table discoverable.
GRANT SELECT ON sys.all_tab_columns TO adf_reader;
GRANT SELECT ON sys.all_tables      TO adf_reader;

-- Per-schema read access. Granting on the schema's tables individually is
-- preferred over SELECT ANY TABLE.
BEGIN
    FOR t IN (SELECT owner, table_name FROM all_tables WHERE owner = 'SALES') LOOP
        EXECUTE IMMEDIATE 'GRANT SELECT ON ' || t.owner || '.' || t.table_name || ' TO adf_reader';
    END LOOP;
END;
/

-- Parallel range reads and long full-table scans need a workable session
-- profile. Adjust to your site's standards.
ALTER USER adf_reader PROFILE default;

-- Optional but recommended for large extracts: a resource group so the
-- ingestion cannot starve OLTP users.
-- BEGIN
--     dbms_resource_manager.create_consumer_group(
--         consumer_group => 'ADF_EXTRACT', comment => 'ADF ingestion');
-- END;
-- /
