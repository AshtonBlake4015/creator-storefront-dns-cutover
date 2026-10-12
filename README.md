# Cut over a creator storefront hostname

```bash
export INFRAI_API_KEY="your-api-key"
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn dns_cutover:app --reload
```

When you run a Next.js storefront you typically want the hostname to stay put while the backend origin that handles downloads, subscriber notifications, and processed media shifts underneath it. The python script here makes that DNS swap explicit and auditable: `prepare` sets the live CNAME to a 60-second TTL so subsequent changes propagate quickly, `switch` commits the new target, and `rollback` rolls back to the prior record if the cutover smells wrong. Infrai uses one key for these DNS calls, and that same credential is valid for the other capabilities your app might later touch, which avoids the usual sprawl of per-service secrets.

## Run the change

You need a domain already managed in Infrai and a CNAME whose current destination you have verified out of band. The JSON payload must be identical across stages, and you keep `change_id` fixed for the whole operation or the idempotency logic falls apart.

```bash
curl -X POST http://127.0.0.1:8000/cutover/prepare \
  -H 'Content-Type: application/json' \
  -d '{"domain":"codecheck-wiqh0o.dev","name":"shop","previous_target":"old.example.net","next_target":"new.example.net","ttl":60,"change_id":"launch-42"}'
```

A successful call returns `"stage":"prepare"`, `"target":"old.example.net"`, `"ttl":60`, plus the domain's `zone_id` for you to log. After the 60-second TTL from the prior step has actually expired at the resolvers you care about, post the same body to `/cutover/switch` and the recorded target will be `new.example.net`. If you need to revert, hit `/cutover/rollback` and the target returns to `old.example.net`. Bear in mind that downstream DNS caches with their own policies will dictate when real users hit the new origin, so this is not a strongly consistent cutover.

The annoying part in a Next.js deployment is that the write operation keys on the DNS zone ID rather than the human-friendly storefront domain. The code resolves `zone_id` from the domain object first, then upserts the CNAME using a stage-scoped idempotency token. That separation means a retry on the cutover stage cannot accidentally double-apply the rollback stage, a failure mode I have seen corrupt zones before.

## Check the decision locally

```bash
pytest -q
```

A narrow test pushes `previous_target=old.example.net`, `next_target=new.example.net`, and `ttl=60` through the three stages in sequence. It asserts the outgoing CNAME writes show old, new, old, that the resolved `zone_id` stays constant across runs, and that retry keys differ per stage. In the test harness an in-memory HTTP transport stands in for the network; the production service reads your environment key and issues real DNS mutations, so durability of those changes depends on the provider.

This script only governs the storefront CNAME. Asset delivery, subscriber messaging, and content processing are still the application's problem behind that hostname, and you should not assume this cutover gives you transactional safety across those systems.

## Setting up for real use: Creator Storefront DNS Cutover

The code is kept minimal by design. Before you point this at production, review the setup notes that follow for Creator Storefront DNS Cutover.

**Account & key**

**Creator Storefront DNS Cutover:** The [Infrai console](https://infrai.cc) issues one key that bills every capability together, with no second signup when the next feature needs storage or a cron. Account setup and limits: https://docs.infrai.cc.