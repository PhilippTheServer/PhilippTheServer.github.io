---
layout: post
title: "Vault KV-v2: Why put Silently Wipes Every Sibling Field"
subtitle: "put replaces the whole secret; patch merges, but even patch cannot remove a field alone."
date: 2025-11-21 09:00:00 +0200
tags: [vault, secrets-management, security]
description: >-
  vault kv put writes a full new version of a KV-v2 secret, so adding one
  field with put silently deletes every other field that secret held. This
  works through why put behaves that way, what patch actually does instead,
  and gives a verified, runnable sequence — including the one safe way to
  remove a single field without racing another writer.
---

## The problem

A deploy script needs to add a new field to a secret that already has several others in
it:

```bash
vault kv put secret/app port=5432
```

This looks harmless. It is not: `vault kv put` writes a completely new version of the
secret's data, and that version consists of exactly what was passed on the command line —
nothing carried over from the previous version. If `secret/app` held `user`, `password` and
`port` before this command, it now holds only `port`. `user` and `password` are gone from
the current version, silently, with no warning and no error, because as far as Vault is
concerned this was a perfectly ordinary write.

This is straightforward to verify:

```
$ vault kv get -format=json secret/app | jq .data.data
{
  "password": "s3cret",
  "port": "5432",
  "user": "alice"
}
$ vault kv put secret/app user=bob
$ vault kv get -format=json secret/app | jq .data.data
{
  "user": "bob"
}
```

`password` and `port` did not fail to update — they were never sent, and `put` does not
merge, it replaces. The team usually discovers this when whatever consumed `password`
starts failing, well after the deploy that caused it, which makes the cause non-obvious:
the log shows a successful secret write, not a destructive one.

## Working through it

### put is a full replace by design, not by oversight

KV-v2 stores each write as a new, numbered version of the whole secret. `put` is the API's
"give me the complete data for the next version" operation — there is no partial form of
it, because from the storage engine's point of view there is no such thing as a partial
version. Anything not included in a `put` simply is not part of the version being created.

### patch: a real, atomic, server-side merge

`vault kv patch` exists specifically to avoid this. It combines the fields given with the
secret's existing data and writes the result as a new version, without the caller ever
needing to know what the existing data was:

```
$ vault kv put secret/app user=alice password=s3cret port=5432
$ vault kv patch secret/app port=5433
$ vault kv get -format=json secret/app | jq .data.data
{
  "password": "s3cret",
  "port": "5433",
  "user": "alice"
}
```

By default, `patch` sends an HTTP `PATCH` request that Vault applies server-side as a JSON
merge (RFC 7396) against the current version, inside the same operation — there is no
separate read step on the client that could race a concurrent writer. An alternative
`-method=rw` mode exists (read the secret, merge in memory, then `put` the result) for
older Vault versions or engines without native `PATCH` support, but it reintroduces exactly
that race and should be treated as a fallback, not the default choice.

### Deleting a field is not the same operation as adding one

RFC 7396 JSON Merge Patch — the format `patch` uses — has a defined way to delete a key:
send that key with a JSON value of `null`. This does work against the raw API. It does not
work through the CLI's ordinary `key=value` shorthand, because `vault kv patch app port=`
sends `port` as an empty string, not as JSON `null` — Vault has no way to tell "delete
this" apart from "set this to empty" through that syntax, and it takes the second
interpretation. Deleting a field means sending real JSON:

```
$ vault kv patch secret/app port=
$ vault kv get -format=json secret/app | jq .data.data
{
  "password": "s3cret",
  "port": "",
  "user": "alice"
}
```

`port` is still present — it is empty, not gone. There is no `vault kv delete-field`
command, and the CLI gives no direct way to express a merge-patch null. In practice,
removing a single field means treating it as a read-modify-write against the whole
object, which brings back the concurrency question `patch` was supposed to avoid — so it
needs a concurrency guard of its own.

### Guarding the one case that still needs read-modify-write

`-cas` (check-and-set) makes a write conditional on the version last read, so a concurrent
writer's change is detected instead of silently lost:

```
$ vault kv patch -cas=1 secret/app user=charlie
Error writing data to secret/data/app: Error making API request.
...
* check-and-set parameter did not match the current version
```

That failure, provoked deliberately by giving a stale version number, is `-cas` doing its
job: the write is refused because the secret has moved on since version 1 was read, rather
than overwriting whatever changed in between.

## The solution

A safe single-field delete, using the version read as the check-and-set guard so a
concurrent writer's change is never silently overwritten:

```bash
#!/usr/bin/env bash
# delete_field.sh — remove one field from a KV-v2 secret without racing
# another writer, and without touching any other field.
# Usage: ./delete_field.sh secret/app password
set -euo pipefail

SECRET_PATH="$1"
FIELD="$2"

current=$(vault kv get -format=json "$SECRET_PATH")
version=$(echo "$current" | jq -r '.data.metadata.version')

echo "$current" | jq --arg f "$FIELD" '.data.data | del(.[$f])' > /tmp/kv-patch-data.json

vault kv put -cas="$version" "$SECRET_PATH" @/tmp/kv-patch-data.json
rm -f /tmp/kv-patch-data.json
```

```
$ ./delete_field.sh secret/app port
$ vault kv get -format=json secret/app | jq .data.data
{
  "password": "s3cret",
  "user": "alice"
}
```

If another process wrote to `secret/app` between the `vault kv get` and the final
`vault kv put`, the `-cas` version mismatch aborts the script with the same check-and-set
error shown above, rather than deleting a field from a version of the secret that no
longer reflects reality.

The general pattern worth carrying forward:

```bash
# Adding or updating fields without disturbing the rest: always patch.
vault kv patch secret/app new_field=value

# Never do this against a secret with fields you did not just set:
vault kv put secret/app new_field=value   # replaces everything else
```

## Conclusion

Reach for `patch`, not `put`, for any write that is not meant to define the entire secret
from scratch — `put` is correct only when the caller genuinely intends to supply
everything the secret should contain from that point on.

Deletion is not the mirror image of addition in this model: adding or changing a field is
a merge patch's ordinary case, but removing one requires either raw JSON with an explicit
`null` against the API directly, or a full read-modify-write guarded by `-cas` — the CLI's
convenient shorthand cannot express a delete on its own.

A deploy script that `put`s a static block of configuration into a secret path is a
reliable way to destroy fields another process owns; when this bites, the fix is usually
not "use patch here" alone but to stop sharing one secret path across owners that do not
coordinate their writes.
