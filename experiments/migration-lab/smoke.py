"""Mechanical workshop checks, not evidence of product superiority."""
import json
import subprocess
import uuid

import psycopg2


def main():
    db = psycopg2.connect(host='127.0.0.1', port=55432, user='node', dbname='postgres')
    schema = 'qualification_' + uuid.uuid4().hex
    try:
        with db.cursor() as c:
            c.execute('CREATE SCHEMA ' + schema)
            c.execute('SET search_path TO ' + schema)
            c.execute('CREATE TABLE customer (id integer primary key, name text NOT NULL)')
            c.execute("INSERT INTO customer VALUES (1, 'Ada')")
            db.commit()
            c.execute('ALTER TABLE customer ADD COLUMN display_name text')
            c.execute('SELECT name FROM customer WHERE id=1')
            assert c.fetchone() == ('Ada',), 'additive safe control failed'
            db.commit()
            c.execute('ALTER TABLE customer RENAME COLUMN name TO old_name')
            db.commit()
            try:
                c.execute('SELECT name FROM customer WHERE id=1')
            except psycopg2.errors.UndefinedColumn:
                db.rollback()
            else:
                raise AssertionError('expected old-client incompatibility was not observed')
            c.execute("UPDATE customer SET old_name='changed' WHERE id=1")
            db.rollback()
            c.execute('SELECT old_name FROM customer WHERE id=1')
            assert c.fetchone() == ('Ada',), 'rollback did not preserve old value'
            c.execute('SHOW fsync')
            assert c.fetchone() == ('on',)
            c.execute('SELECT version()')
            version = c.fetchone()[0]
        print(json.dumps(dict(postgres=version, atlas=subprocess.check_output(
            ['atlas', 'version'], text=True).strip(), checks=['additive_old_client',
            'breaking_old_client', 'rollback', 'fsync_on'], evidence='setup qualification only')))
    finally:
        db.rollback()
        with db.cursor() as c:
            c.execute('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE')
        db.commit()
        db.close()


if __name__ == '__main__':
    main()

