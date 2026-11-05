#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ -f .env ]; then
 echo '.env už existuje; konfigurácia a heslá zostali zachované.'
 exit 0
fi
umask 077
# Hexadecimal secrets are safe in both Compose expansion and database URLs.
secret() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }
db_password=$(secret)
sso_key=$(secret)
demo_password=$(secret | cut -c1-20)
cat > .env <<CONFIG
PORT=8090
BIND_ADDRESS=0.0.0.0
COOKIE_SECURE=0
POSTGRES_PASSWORD=$db_password
SSO_INTERNAL_KEY=$sso_key
DEMO_PASSWORD=$demo_password
GEMINI_API_KEY=
CONFIG
printf '\nTestovacie účty: maros (správca), zdenko, florian, martin\nPočiatočné heslo pre všetky účty: %s\n\nSpustenie: docker compose up -d --build\nAdresa: http://IP_SERVERA:8090\n' "$demo_password"
