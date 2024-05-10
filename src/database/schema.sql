CREATE DATABASE IF NOT EXISTS MyTestDB;

USE MyTestDB;

CREATE TABLE IF NOT EXISTS logs (
    unique_time DECIMAL(20,10) NOT NULL, -- 10 digits for datetime, 6 for microseconds, 4 for incremental id
    mask BIT(40) NOT NULL, -- extended inotify mask
    src_path VARCHAR(255) NOT NULL, -- path to file
    dest_path VARCHAR(255), -- path to dest file for rename event
    monitor_pid INT UNSIGNED NOT NULL, -- pid of monitor process
    PRIMARY KEY (unique_time)
);

CREATE TABLE IF NOT EXISTS tracked_index (
    fid INT NOT NULL AUTO_INCREMENT,
    path VARCHAR(255) NOT NULL,
    version SMALLINT UNSIGNED NOT NULL,
    format CHAR(3) NOT NULL, -- file format: INI, GEN(ERIC)
    PRIMARY KEY (fid)
);