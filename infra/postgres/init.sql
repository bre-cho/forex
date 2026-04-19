-- PostgreSQL initialization script
CREATE DATABASE forex_db;
CREATE USER forex WITH ENCRYPTED PASSWORD 'forex_dev';
GRANT ALL PRIVILEGES ON DATABASE forex_db TO forex;
\connect forex_db;
GRANT ALL ON SCHEMA public TO forex;
