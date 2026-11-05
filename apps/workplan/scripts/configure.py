"""Create local secrets without overwriting an existing configuration."""
import os
import secrets
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=root/'.env'
if p.exists(): raise SystemExit('.env už existuje; konfigurácia sa nemení.')
text=(root/'.env.example').read_text()
text=text.replace('replace_with_random_hex_database_password',secrets.token_hex(24))
text=text.replace('replace_with_random_secret_at_least_32_characters',secrets.token_hex(48))
text=text.replace('change_this_demo_password',secrets.token_urlsafe(18))
fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as f:f.write(text)
print('Konfigurácia vytvorená. Demo heslo nájdete v .env v položke DEMO_PASSWORD.')
