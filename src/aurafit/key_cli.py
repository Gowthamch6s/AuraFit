"""One-time provisioning of an opaque, 256-bit per-user bearer token."""
import argparse
import hashlib
import secrets
from uuid import UUID
import psycopg


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--user-id',type=UUID,required=True)
    args=parser.parse_args()
    secret='af_'+secrets.token_hex(32)
    with psycopg.connect(args.database_url) as conn:
        with conn.transaction():
            conn.execute('INSERT INTO aurafit.api_keys(user_id,token_sha256) VALUES (%s,%s)',(args.user_id,hashlib.sha256(secret.encode()).hexdigest()))
    print('SAVE THIS TOKEN SECURELY; IT WILL NOT BE DISPLAYED AGAIN:')
    print(secret)
if __name__=='__main__':
    main()
